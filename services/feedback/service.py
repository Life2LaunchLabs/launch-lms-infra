"""Durable feedback submission coordination with Jira as canonical record."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import BinaryIO

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.connectors.tracker import TrackerAdapter


IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class SubmissionConflict(ValueError):
    pass


class SubmissionPending(RuntimeError):
    def __init__(self, result: dict):
        super().__init__("Feedback was accepted and is pending tracker synchronization")
        self.result = result


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    stream: BinaryIO


@dataclass(frozen=True)
class Submission:
    message: str
    intent: str | None
    context: dict
    attachments: tuple[Attachment, ...] = ()


def _fingerprint(value: Submission) -> str:
    canonical = {
        "message": value.message, "intent": value.intent, "context": value.context,
        "attachments": [{"filename": item.filename, "content_type": item.content_type}
                        for item in value.attachments],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _description(value: Submission) -> dict:
    context = json.dumps(value.context, indent=2, sort_keys=True)
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": value.message}]},
        {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Anonymous reproduction context"}]},
        {"type": "codeBlock", "attrs": {"language": "json"}, "content": [{"type": "text", "text": context}]},
    ]}


class FeedbackService:
    def __init__(self, tracker: TrackerAdapter, project_key: str):
        self.tracker = tracker
        self.project_key = project_key

    @staticmethod
    def _record(session: Session, project_id: str, key_hash: str):
        from models import IdempotencyRecord
        return session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.project_id == project_id,
            IdempotencyRecord.operation == "feedback.create",
            IdempotencyRecord.key_hash == key_hash,
        ))

    async def submit(self, session: Session, *, project_id: str, environment: str,
                     opaque_user_id: str, opaque_org_id: str, idempotency_key: str,
                     submission: Submission) -> tuple[dict, bool]:
        from models import IdempotencyRecord

        if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Idempotency-Key must be 16-128 URL-safe characters")
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        fingerprint = _fingerprint(submission)
        record = self._record(session, project_id, key_hash)
        if not record:
            result = {
                "fingerprint": fingerprint, "owner_user": opaque_user_id,
                "owner_org": opaque_org_id, "issue_key": None,
                "attachments": [{"filename": item.filename, "status": "pending"}
                                for item in submission.attachments],
                "pending_payload": {
                    "message": submission.message, "intent": submission.intent,
                    "context": submission.context,
                },
            }
            record = IdempotencyRecord(
                project_id=project_id, operation="feedback.create", key_hash=key_hash,
                status="pending", result=result,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                record = self._record(session, project_id, key_hash)
        if not record:
            raise RuntimeError("Unable to establish feedback idempotency record")
        result = dict(record.result or {})
        if result.get("fingerprint") != fingerprint:
            raise SubmissionConflict("Idempotency-Key was already used for different feedback")
        if result.get("owner_user") != opaque_user_id or result.get("owner_org") != opaque_org_id:
            raise SubmissionConflict("Idempotency-Key belongs to a different session")
        if record.status == "synchronized":
            return result, False

        properties = {"launch-operations": {
            "project": project_id, "environment": environment,
            "opaque_user_id": opaque_user_id, "opaque_organization_id": opaque_org_id,
            "intent": submission.intent, "synchronization_revision": 1,
            "idempotency_hash": key_hash,
        }}
        try:
            if not result.get("issue_key"):
                summary = submission.message.splitlines()[0].strip()[:200] or "Tester feedback"
                issue = await self.tracker.create_issue(
                    self.project_key, summary, _description(submission), properties, idempotency_key,
                )
                result["issue_key"] = issue.key
                result["revision"] = issue.revision
                record.result = dict(result)
                session.add(record); session.commit()

            attachments = list(result.get("attachments", []))
            for index, attachment in enumerate(submission.attachments):
                if attachments[index].get("status") == "synchronized":
                    continue
                uploaded = await self.tracker.attach(
                    result["issue_key"], attachment.filename, attachment.content_type, attachment.stream,
                )
                attachments[index] = {
                    "filename": attachment.filename, "status": "synchronized",
                    "tracker_id": str(uploaded.get("id", "")),
                }
                result["attachments"] = attachments
                record.result = dict(result)
                session.add(record); session.commit()
        except (httpx.HTTPError, OSError, TimeoutError):
            record.status = "attachment_failed" if result.get("issue_key") else "pending"
            result["pending_reason"] = "tracker_unavailable"
            record.result = dict(result)
            session.add(record); session.commit()
            raise SubmissionPending(result)

        record.status = "synchronized"
        result.pop("pending_payload", None)
        result.pop("pending_reason", None)
        result["status"] = "synchronized"
        record.result = dict(result)
        session.add(record); session.commit()
        return result, True

    def status(self, session: Session, *, project_id: str, opaque_user_id: str,
               opaque_org_id: str, idempotency_key: str) -> dict | None:
        if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key")
        record = self._record(session, project_id, hashlib.sha256(idempotency_key.encode()).hexdigest())
        if not record:
            return None
        result = dict(record.result or {})
        if result.get("owner_user") != opaque_user_id or result.get("owner_org") != opaque_org_id:
            return None
        return {
            "status": record.status, "issue_key": result.get("issue_key"),
            "attachments": result.get("attachments", []), "updated_at": record.updated_at.isoformat(),
        }

    @staticmethod
    def _metadata(issue) -> dict:
        value = issue.properties.get("launch-operations", {}) if isinstance(issue.properties, dict) else {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _adf_text(value) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, dict):
            return ""
        if value.get("type") == "text":
            return str(value.get("text", ""))
        return "".join(FeedbackService._adf_text(item) for item in value.get("content", []))

    @staticmethod
    def _comment_entry(comment: dict) -> dict | None:
        text = FeedbackService._adf_text(comment.get("body")).strip()
        prefixes = (("[Launch LMS reply]", "operator"), ("[Launch LMS tester comment]", "tester"))
        match = next(((prefix, author) for prefix, author in prefixes if text.startswith(prefix)), None)
        if not match:
            return None
        prefix, author = match
        return {
            "id": str(comment.get("id", "")), "author": author,
            "message": text.removeprefix(prefix).strip(), "created_at": comment.get("created"),
        }

    @staticmethod
    def _initial_message(issue) -> str:
        description = issue.fields.get("description", {}) if isinstance(issue.fields, dict) else {}
        content = description.get("content", []) if isinstance(description, dict) else []
        first_block = content[0] if content else None
        return FeedbackService._adf_text(first_block).strip() or issue.summary

    @staticmethod
    def _matches(metadata: dict, *, project_id: str, environment: str,
                 opaque_user_id: str, opaque_org_id: str) -> bool:
        return all((
            metadata.get("project") == project_id,
            metadata.get("environment") == environment,
            metadata.get("opaque_user_id") == opaque_user_id,
            metadata.get("opaque_organization_id") == opaque_org_id,
        ))

    async def conversations(self, session: Session, *, project_id: str, environment: str,
                            opaque_user_id: str, opaque_org_id: str) -> list[dict]:
        from models import UnreadMarker

        markers = {item.conversation_id: item for item in session.scalars(select(UnreadMarker).where(
            UnreadMarker.project_id == project_id,
            UnreadMarker.environment == environment,
            UnreadMarker.opaque_user_id == opaque_user_id,
        )).all()}
        result = []
        for issue in await self.tracker.list_issues(self.project_key):
            metadata = self._metadata(issue)
            if not self._matches(metadata, project_id=project_id, environment=environment,
                                 opaque_user_id=opaque_user_id, opaque_org_id=opaque_org_id):
                continue
            entries = [entry for comment in await self.tracker.comments(issue.key)
                       if (entry := self._comment_entry(comment)) is not None]
            visible = json.dumps(entries, sort_keys=True, separators=(",", ":"))
            revision = issue.revision + ":" + hashlib.sha256(visible.encode()).hexdigest()[:16]
            marker = markers.get(issue.key)
            status_changed = issue.status.casefold() not in {"open", "new", "to do"}
            attachments = [{
                "id": str(item.get("id", "")), "filename": str(item.get("filename", "")),
                "content_type": str(item.get("mimeType", "")), "size": int(item.get("size", 0) or 0),
            } for item in issue.fields.get("attachment", [])]
            result.append({
                "key": issue.key, "message": self._initial_message(issue), "status": issue.status,
                "intent": metadata.get("intent"), "revision": revision, "entries": entries,
                "attachments": attachments,
                "has_unread": marker.seen_revision != revision if marker else
                              (status_changed or any(entry["author"] == "operator" for entry in entries)),
            })
        return result

    async def owned_issue(self, key: str, *, project_id: str, environment: str,
                          opaque_user_id: str, opaque_org_id: str):
        issue = await self.tracker.get_issue(key)
        if not self._matches(self._metadata(issue), project_id=project_id, environment=environment,
                             opaque_user_id=opaque_user_id, opaque_org_id=opaque_org_id):
            raise LookupError("Feedback conversation not found")
        return issue

    async def tester_reply(self, key: str, message: str, **identity) -> None:
        await self.owned_issue(key, **identity)
        await self.tracker.add_comment(key, {
            "type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "[Launch LMS tester comment] " + message},
            ]}],
        }, public=False)

    async def confirm_resolution(self, key: str, outcome: str, **identity) -> None:
        issue = await self.owned_issue(key, **identity)
        metadata = self._metadata(issue)
        metadata = {**metadata, "tester_resolution": outcome,
                    "synchronization_revision": int(metadata.get("synchronization_revision", 1)) + 1}
        text = "Resolution confirmed" if outcome == "looks_good" else "The problem is still happening"
        await self.tracker.add_comment(key, {
            "type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "[Launch LMS tester comment] " + text},
            ]}],
        }, public=False)
        await self.tracker.set_properties(key, {"launch-operations": metadata})

    def mark_viewed(self, session: Session, *, project_id: str, environment: str,
                    opaque_user_id: str, conversation_id: str, revision: str) -> None:
        from models import UnreadMarker

        marker = session.scalar(select(UnreadMarker).where(
            UnreadMarker.project_id == project_id, UnreadMarker.environment == environment,
            UnreadMarker.opaque_user_id == opaque_user_id,
            UnreadMarker.conversation_id == conversation_id,
        )) or UnreadMarker(
            project_id=project_id, environment=environment, opaque_user_id=opaque_user_id,
            conversation_id=conversation_id, seen_revision=revision,
        )
        marker.seen_revision = revision
        marker.updated_at = datetime.now(timezone.utc)
        session.add(marker); session.commit()
