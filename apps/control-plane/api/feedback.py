"""Authenticated embed feedback endpoints backed by durable Jira coordination."""

from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import Settings
from embed import database_session, require_embed_session
from models import EmbedSession
from packages.connectors.jira import JiraAdapter, JiraCredentials
from services.feedback.service import Attachment, FeedbackService, Submission, SubmissionConflict, SubmissionPending
from services.orchestrator.manifest import load_project


router = APIRouter()
INTENTS = {"stuck", "broken", "confusing", "missing", "love"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
STATIC_SEGMENTS = {
    "orgs", "hub", "portfolio", "projects", "plans", "plan", "live", "assignments",
    "badges", "learning-path", "resources", "communities", "community", "discussion",
    "account", "admin", "platform", "users", "settings", "feedback", "analytics",
}


class FeedbackReply(BaseModel):
    message: str


class FeedbackResolution(BaseModel):
    outcome: Literal["looks_good", "still_happening"]


class FeedbackViewed(BaseModel):
    revision: str


def _route(value: object) -> str:
    parts = [part for part in str(value or "").split("?", 1)[0].split("#", 1)[0].split("/") if part]
    sanitized = [part if part.casefold() in STATIC_SEGMENTS else ":id" for part in parts[:12]]
    return "/" + "/".join(sanitized) if sanitized else "/"


def _dimension(value: object) -> int:
    try:
        return min(max(int(value or 0), 0), 20_000)
    except (TypeError, ValueError):
        return 0


def sanitize_context(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    viewport = value.get("viewport") if isinstance(value.get("viewport"), dict) else {}
    release = str(value.get("release") or "unknown")
    if release != "unknown" and not re.fullmatch(r"(?:sha-)?[0-9a-f]{7,40}", release):
        release = "unknown"
    return {
        "route": _route(value.get("route")),
        "theme": "dark" if value.get("theme") == "dark" else "light",
        "viewport": {"width": _dimension(viewport.get("width")), "height": _dimension(viewport.get("height"))},
        "release": release,
    }


def service(request: Request) -> FeedbackService:
    settings: Settings = request.app.state.settings
    manifest = load_project("launch-lms")
    credentials = JiraCredentials(settings.jira_base_url, settings.jira_feedback_email, settings.jira_feedback_token)
    return FeedbackService(JiraAdapter(credentials), manifest.data["tracker"]["feedback_project"])


def identity_arguments(identity: EmbedSession) -> dict:
    return {
        "project_id": identity.project_id, "environment": identity.environment,
        "opaque_user_id": identity.opaque_user_id, "opaque_org_id": identity.opaque_org_id,
    }


@router.post("/api/v1/embed/feedback")
async def create_feedback(
    request: Request,
    response: Response,
    message: str = Form(...),
    intent: str | None = Form(default=None),
    context: str | None = Form(default=None),
    images: list[UploadFile] = File(default=[]),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    identity: EmbedSession = Depends(require_embed_session),
    session: Session = Depends(database_session),
) -> dict:
    clean_message = message.strip()
    clean_intent = str(intent or "").strip().casefold() or None
    if not clean_message or len(clean_message) > 10_000:
        raise HTTPException(422, "Feedback must contain 1-10,000 characters")
    if clean_intent not in INTENTS | {None}:
        raise HTTPException(422, "Unknown feedback intent")
    if len(images) > 3:
        raise HTTPException(422, "Attach up to three screenshots")
    for image in images:
        if image.content_type not in IMAGE_TYPES or (image.size is not None and image.size > 10 * 1024 * 1024):
            raise HTTPException(422, "Attachments must be PNG, JPEG, or WebP images up to 10 MB")
    if not idempotency_key:
        raise HTTPException(422, "Idempotency-Key is required")
    submission = Submission(
        message=clean_message, intent=clean_intent, context=sanitize_context(context),
        attachments=tuple(Attachment(image.filename or "screenshot", image.content_type or "", image.file) for image in images),
    )
    try:
        result, created = await service(request).submit(
            session, project_id=identity.project_id, environment=identity.environment,
            opaque_user_id=identity.opaque_user_id, opaque_org_id=identity.opaque_org_id,
            idempotency_key=idempotency_key, submission=submission,
        )
    except SubmissionConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except SubmissionPending as pending:
        response.status_code = 202
        return {"status": "pending", "issue_key": pending.result.get("issue_key")}
    response.status_code = 201 if created else 200
    return {"status": "synchronized", "issue_key": result["issue_key"]}


@router.get("/api/v1/embed/feedback")
async def list_feedback(
    request: Request,
    identity: EmbedSession = Depends(require_embed_session),
    session: Session = Depends(database_session),
) -> list[dict]:
    return await service(request).conversations(session, **identity_arguments(identity))


@router.post("/api/v1/embed/feedback/{issue_key}/reply", status_code=204)
async def reply_to_feedback(
    issue_key: str,
    body: FeedbackReply,
    request: Request,
    identity: EmbedSession = Depends(require_embed_session),
) -> Response:
    message = body.message.strip()
    if not message or len(message) > 10_000:
        raise HTTPException(422, "Reply must contain 1-10,000 characters")
    try:
        await service(request).tester_reply(issue_key, message, **identity_arguments(identity))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return Response(status_code=204)


@router.post("/api/v1/embed/feedback/{issue_key}/resolution", status_code=204)
async def resolve_feedback(
    issue_key: str,
    body: FeedbackResolution,
    request: Request,
    identity: EmbedSession = Depends(require_embed_session),
) -> Response:
    try:
        await service(request).confirm_resolution(
            issue_key, body.outcome, **identity_arguments(identity),
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return Response(status_code=204)


@router.post("/api/v1/embed/feedback/{issue_key}/viewed", status_code=204)
async def feedback_viewed(
    issue_key: str,
    body: FeedbackViewed,
    request: Request,
    identity: EmbedSession = Depends(require_embed_session),
    session: Session = Depends(database_session),
) -> Response:
    if not body.revision or len(body.revision) > 128:
        raise HTTPException(422, "Feedback revision is invalid")
    api = service(request)
    try:
        await api.owned_issue(issue_key, **identity_arguments(identity))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    api.mark_viewed(
        session, project_id=identity.project_id, environment=identity.environment,
        opaque_user_id=identity.opaque_user_id, conversation_id=issue_key,
        revision=body.revision,
    )
    return Response(status_code=204)


@router.get("/api/v1/embed/feedback/submissions/{idempotency_key}")
def feedback_status(
    request: Request,
    idempotency_key: str,
    identity: EmbedSession = Depends(require_embed_session),
    session: Session = Depends(database_session),
) -> dict:
    try:
        result = service(request).status(
            session, project_id=identity.project_id, opaque_user_id=identity.opaque_user_id,
            opaque_org_id=identity.opaque_org_id, idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if result is None:
        raise HTTPException(404, "Feedback submission not found")
    return result
