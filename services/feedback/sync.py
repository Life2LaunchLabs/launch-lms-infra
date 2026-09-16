"""Synchronize durable pending feedback operations with canonical Jira issues."""

from __future__ import annotations

import json
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session


PROPERTY = "launchlms.feedback"
TESTER_PREFIX = "[Launch LMS tester comment]"


def adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [{
        "type": "paragraph", "content": [{"type": "text", "text": text[:28_000]}],
    }]}


def adf_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if value.get("type") == "text":
        return str(value.get("text", ""))
    return "".join(adf_text(child) for child in value.get("content", []))


STATIC_ROUTE_SEGMENTS = {
    "orgs", "hub", "portfolio", "projects", "preview", "plans", "plan", "live",
    "assignments", "requirements", "badges", "learning-path", "resources", "communities",
    "community", "discussion", "store", "offers", "account", "admin", "platform", "settings",
}


def sanitize_route(value: str) -> str:
    path = str(value or "").split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in path.split("/") if part]
    safe = [part if part.casefold() in STATIC_ROUTE_SEGMENTS else ":id" for part in parts[:12]]
    return "/" + "/".join(safe) if safe else "/"


def sanitize_context(value: dict) -> dict:
    context = {key: value.get(key) for key in ("theme", "viewport", "release") if key in value}
    context["route"] = sanitize_route(value.get("route", ""))
    return context


async def synchronize_pending(engine, adapter, manifest: dict, limit: int = 25) -> dict:
    from models import IdempotencyRecord, PendingAttachment

    with Session(engine) as session:
        records = list(session.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.project_id == manifest["project_id"],
            IdempotencyRecord.operation == "feedback.create",
            IdempotencyRecord.status == "pending",
        ).order_by(IdempotencyRecord.id).limit(limit)))
    synced = failed = awaiting = 0
    for record in records:
        pending = dict(record.result or {})
        try:
            with Session(engine) as session:
                attachments = list(session.scalars(select(PendingAttachment).where(
                    PendingAttachment.operation_id == record.id,
                ).order_by(PendingAttachment.created_at)))
            if len(attachments) < int(pending.get("attachment_count", 0)):
                awaiting += 1
                continue
            message = str(pending["message"]).strip()
            context = sanitize_context(pending.get("context") or {})
            metadata = {
                "project": record.project_id, "environment": pending["environment"],
                "opaque_user_id": pending["opaque_user_id"], "opaque_organization_id": pending["opaque_org_id"],
                "role": pending["role"], "intent": pending.get("intent"),
                "synchronization_revision": str(record.id), "synchronization_status": "pending",
            }
            issue_key = pending.get("issue_key")
            if issue_key:
                issue = await adapter.get_issue(issue_key)
            else:
                description = adf(message + "\n\nAnonymous reproduction context\n" + json.dumps(context, sort_keys=True, indent=2))
                issue = await adapter.create_issue(
                    manifest["tracker"]["feedback_project"], message.splitlines()[0][:110],
                    description, {PROPERTY: metadata}, f"feedback-{record.id}",
                )
                issue_key = issue.key
                pending["issue_key"] = issue_key
                with Session(engine) as session:
                    current = session.get(IdempotencyRecord, record.id)
                    current.result = pending
                    session.commit()
            for attachment in attachments:
                if attachment.status == "synced":
                    continue
                uploaded = await adapter.attach(
                    issue_key, attachment.filename, attachment.content_type, BytesIO(attachment.content),
                )
                with Session(engine) as session:
                    current_attachment = session.get(PendingAttachment, attachment.id)
                    current_attachment.status = "synced"
                    current_attachment.tracker_attachment_id = str(uploaded.get("id", ""))
                    session.commit()
            metadata["synchronization_status"] = "complete"
            await adapter.set_properties(issue_key, {PROPERTY: metadata})
            issue = await adapter.get_issue(issue_key)
            with Session(engine) as session:
                current = session.get(IdempotencyRecord, record.id)
                current.status = "synced"
                current.result = {"issue_key": issue.key, "revision": issue.revision}
                for attachment in session.scalars(select(PendingAttachment).where(PendingAttachment.operation_id == record.id)):
                    session.delete(attachment)
                session.commit()
            synced += 1
        except Exception as error:
            with Session(engine) as session:
                current = session.get(IdempotencyRecord, record.id)
                current.status = "pending"
                current.result = {**pending, "last_error": type(error).__name__}
                session.commit()
            failed += 1
    return {"examined": len(records), "synced": synced, "pending": failed, "awaiting_attachments": awaiting}


async def conversations(adapter, manifest: dict, identity: dict) -> list[dict]:
    issues = await adapter.search(manifest["tracker"]["feedback_project"], 'labels is EMPTY OR labels is not EMPTY')
    result = []
    for issue in issues:
        metadata = await adapter.property(issue.key, PROPERTY) or {}
        if metadata.get("synchronization_status") != "complete":
            continue
        if metadata.get("project") != identity["project"]:
            continue
        if metadata.get("environment") != identity["environment"] or metadata.get("opaque_user_id") != identity["sub"]:
            continue
        comments = []
        for value in issue.details.get("comment", {}).get("comments", []):
            text = adf_text(value.get("body"))
            if text.startswith("[Launch LMS internal note]"):
                continue
            if text.startswith("[Launch LMS reply]") or text.startswith(TESTER_PREFIX):
                comments.append({"id": value.get("id"), "body": text, "created": value.get("created")})
        result.append({
            "key": issue.key, "summary": issue.summary, "status": issue.status,
            "revision": issue.revision, "created": issue.details.get("created"),
            "comments": comments,
            "attachments": [{"id": item.get("id"), "filename": item.get("filename"), "mime_type": item.get("mimeType")}
                            for item in issue.details.get("attachment", [])],
        })
    return result
