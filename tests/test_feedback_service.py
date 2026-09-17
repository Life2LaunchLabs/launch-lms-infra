"""Durability and isolation tests for platform-owned feedback submission."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/control-plane/api"))

import models
import feedback as feedback_api
from packages.connectors.tracker import TrackerAdapter, TrackerIssue
from services.feedback.service import Attachment, FeedbackService, Submission, SubmissionConflict, SubmissionPending


class FakeTracker(TrackerAdapter):
    def __init__(self):
        self.issue_calls = 0
        self.attach_calls = []
        self.fail_issue = False
        self.fail_attachment = None
        self.issue = None
        self.comment_values = []

    async def create_issue(self, project, summary, description, properties, idempotency_key):
        self.issue_calls += 1
        if self.fail_issue:
            raise httpx.ConnectError("offline")
        self.issue = TrackerIssue("FEED-7", summary, "Open", "r1", properties, {"attachment": []})
        return self.issue

    async def attach(self, key, filename, content_type, stream):
        self.attach_calls.append(filename)
        if filename == self.fail_attachment:
            raise httpx.ConnectError("attachment offline")
        return {"id": len(self.attach_calls), "filename": filename}

    async def get_issue(self, key):
        if not self.issue or self.issue.key != key: raise LookupError(key)
        return self.issue
    async def list_issues(self, project): return [self.issue] if self.issue else []
    async def comments(self, key): return list(self.comment_values)
    async def add_comment(self, key, body, public):
        value = {"id": str(len(self.comment_values) + 1), "body": body, "created": "2026-09-17T00:00:00Z"}
        self.comment_values.append(value)
        return value
    async def transition(self, key, status): self.issue.status = status
    async def set_properties(self, key, properties): self.issue.properties.update(properties)


def submission(message="The save button is stuck", names=()):
    return Submission(
        message=message, intent="stuck",
        context={"route": "/orgs/:id/plans", "theme": "light", "viewport": {"width": 390, "height": 844}},
        attachments=tuple(Attachment(name, "image/png", BytesIO(b"image")) for name in names),
    )


class FeedbackServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True))
            session.commit()
        self.tracker = FakeTracker()
        self.service = FeedbackService(self.tracker, "FEED")
        self.arguments = {
            "project_id": "launch-lms", "environment": "unstable",
            "opaque_user_id": "a" * 64, "opaque_org_id": "b" * 64,
            "idempotency_key": "feedback-operation-0001",
        }

    async def test_synchronized_duplicate_returns_same_issue_without_duplicate_jira_work(self):
        with Session(self.engine) as session:
            result, created = await self.service.submit(session, **self.arguments, submission=submission(names=("one.png",)))
            duplicate, duplicate_created = await self.service.submit(session, **self.arguments, submission=submission(names=("one.png",)))
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(result["issue_key"], duplicate["issue_key"])
        self.assertEqual(self.tracker.issue_calls, 1)
        self.assertEqual(self.tracker.attach_calls, ["one.png"])
        self.assertNotIn("pending_payload", result)
        with Session(self.engine) as session:
            record = session.query(models.IdempotencyRecord).one()
            self.assertNotIn("The save button is stuck", str(record.result))

    async def test_tracker_outage_is_durable_and_retryable(self):
        self.tracker.fail_issue = True
        with Session(self.engine) as session, self.assertRaises(SubmissionPending):
            await self.service.submit(session, **self.arguments, submission=submission())
        with Session(self.engine) as session:
            pending = self.service.status(session, **{key: self.arguments[key] for key in ("project_id", "opaque_user_id", "opaque_org_id", "idempotency_key")})
            self.assertEqual(pending["status"], "pending")
        self.tracker.fail_issue = False
        with Session(self.engine) as session:
            result, created = await self.service.submit(session, **self.arguments, submission=submission())
        self.assertTrue(created)
        self.assertEqual(result["issue_key"], "FEED-7")

    async def test_partial_attachment_failure_never_reports_complete_and_resumes(self):
        self.tracker.fail_attachment = "two.png"
        with Session(self.engine) as session, self.assertRaises(SubmissionPending):
            await self.service.submit(session, **self.arguments, submission=submission(names=("one.png", "two.png")))
        with Session(self.engine) as session:
            pending = self.service.status(session, **{key: self.arguments[key] for key in ("project_id", "opaque_user_id", "opaque_org_id", "idempotency_key")})
            self.assertEqual(pending["status"], "attachment_failed")
            self.assertEqual([item["status"] for item in pending["attachments"]], ["synchronized", "pending"])
        self.tracker.fail_attachment = None
        with Session(self.engine) as session:
            result, _ = await self.service.submit(session, **self.arguments, submission=submission(names=("one.png", "two.png")))
        self.assertEqual(result["status"], "synchronized")
        self.assertEqual(self.tracker.attach_calls, ["one.png", "two.png", "two.png"])

    async def test_idempotency_key_cannot_cross_payload_or_identity(self):
        with Session(self.engine) as session:
            await self.service.submit(session, **self.arguments, submission=submission())
            with self.assertRaises(SubmissionConflict):
                await self.service.submit(session, **self.arguments, submission=submission("Different"))
            changed = dict(self.arguments); changed["opaque_user_id"] = "c" * 64
            with self.assertRaises(SubmissionConflict):
                await self.service.submit(session, **changed, submission=submission())

    async def test_history_filters_internal_notes_and_owns_unread_state(self):
        with Session(self.engine) as session:
            await self.service.submit(session, **self.arguments, submission=submission())
        self.tracker.issue.fields["description"] = {
            "type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Full original feedback"}]},
                {"type": "codeBlock", "content": [{"type": "text", "text": "private context"}]},
            ],
        }
        def adf(text):
            return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
        self.tracker.comment_values = [
            {"id": "1", "body": adf("[Launch LMS internal note] private"), "created": "one"},
            {"id": "2", "body": adf("[Launch LMS reply] Please retry"), "created": "two"},
            {"id": "3", "body": adf("[Launch LMS tester comment] Still broken"), "created": "three"},
        ]
        with Session(self.engine) as session:
            values = await self.service.conversations(session, **{key: self.arguments[key] for key in
                ("project_id", "environment", "opaque_user_id", "opaque_org_id")})
            self.assertEqual([entry["author"] for entry in values[0]["entries"]], ["operator", "tester"])
            self.assertEqual(values[0]["message"], "Full original feedback")
            self.assertTrue(values[0]["has_unread"])
            self.assertNotIn("private", str(values))
            self.service.mark_viewed(
                session, project_id="launch-lms", environment="unstable",
                opaque_user_id="a" * 64, conversation_id="FEED-7", revision=values[0]["revision"],
            )
        with Session(self.engine) as session:
            values = await self.service.conversations(session, **{key: self.arguments[key] for key in
                ("project_id", "environment", "opaque_user_id", "opaque_org_id")})
            self.assertFalse(values[0]["has_unread"])

    async def test_tester_reply_and_resolution_remain_public_and_identity_scoped(self):
        with Session(self.engine) as session:
            await self.service.submit(session, **self.arguments, submission=submission())
        identity = {key: self.arguments[key] for key in
                    ("project_id", "environment", "opaque_user_id", "opaque_org_id")}
        await self.service.tester_reply("FEED-7", "Here is more detail", **identity)
        await self.service.confirm_resolution("FEED-7", "looks_good", **identity)
        self.assertIn("[Launch LMS tester comment] Here is more detail", str(self.tracker.comment_values[0]))
        self.assertEqual(self.tracker.issue.properties["launch-operations"]["tester_resolution"], "looks_good")
        other = dict(identity); other["opaque_org_id"] = "c" * 64
        with self.assertRaises(LookupError):
            await self.service.tester_reply("FEED-7", "cross tenant", **other)

    def test_browser_context_removes_route_identifiers_and_untrusted_release(self):
        value = feedback_api.sanitize_context(json.dumps({
            "route": "/orgs/acme/plans/3f2504e0-4f89-41d3-9a0c-0305e82c3301?token=nope",
            "theme": "dark", "viewport": {"width": 390, "height": 999999},
            "release": "not a sha or version",
        }))
        self.assertEqual(value["route"], "/orgs/:id/plans/:id")
        self.assertEqual(value["viewport"], {"width": 390, "height": 20_000})
        self.assertEqual(value["release"], "unknown")


if __name__ == "__main__":
    unittest.main()
