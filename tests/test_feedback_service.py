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

    async def create_issue(self, project, summary, description, properties, idempotency_key):
        self.issue_calls += 1
        if self.fail_issue:
            raise httpx.ConnectError("offline")
        return TrackerIssue("FEED-7", summary, "Open", "r1", properties)

    async def attach(self, key, filename, content_type, stream):
        self.attach_calls.append(filename)
        if filename == self.fail_attachment:
            raise httpx.ConnectError("attachment offline")
        return {"id": len(self.attach_calls), "filename": filename}

    async def get_issue(self, key): raise NotImplementedError
    async def comments(self, key): raise NotImplementedError
    async def add_comment(self, key, body, public): raise NotImplementedError
    async def transition(self, key, status): raise NotImplementedError
    async def set_properties(self, key, properties): raise NotImplementedError


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
