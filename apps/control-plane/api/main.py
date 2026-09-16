"""Authenticated operations control-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys

import httpx
from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(os.getenv("OPERATIONS_ROOT", str(Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(ROOT))

from services.orchestrator.manifest import load_project  # noqa: E402
from services.deployer.candidate import validate_candidate  # noqa: E402
from packages.connectors.github import GitHubAppCredentials  # noqa: E402
from packages.connectors.jira import JiraAdapter, JiraCredentials  # noqa: E402
from services.feedback.sync import adf, conversations, synchronize_pending  # noqa: E402
from auth import COOKIE, create_session, exchange_and_authorize, login_url, require_operator  # noqa: E402
from config import Settings  # noqa: E402
from embed_auth import (DatabaseReplayStore, MemoryReplayStore, PROTOCOL, create_platform_session,
                        origin_allowed, verify_host_token, verify_platform_session)  # noqa: E402
from models import Announcement, DeploymentObservation, IdempotencyRecord, PendingAttachment, Project, UnreadMarker  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings()
    if app.state.settings.environment == "development":
        app.state.replay_store = MemoryReplayStore()
        app.state.engine = None
    else:
        engine = create_engine(app.state.settings.database_url, pool_pre_ping=True)
        manifest = load_project("launch-lms")
        revision = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
        with Session(engine) as session:
            session.merge(Project(id=manifest.project_id, manifest_revision=revision, enabled=True))
            session.commit()
        app.state.replay_store = DatabaseReplayStore(engine)
        app.state.engine = engine
    yield


app = FastAPI(title="Launch Operations", version="0.1.0", lifespan=lifespan)


class CandidateDispatch(BaseModel):
    candidate: dict


class DeploymentResult(BaseModel):
    run_id: int
    source_sha: str
    image_digest: str


class EmbedVerification(BaseModel):
    project: str
    environment: str
    nonce: str
    host_origin: str
    token: str


class FeedbackSubmission(BaseModel):
    message: str
    context: dict = Field(default_factory=dict)
    intent: str | None = None
    attachment_count: int = Field(default=0, ge=0, le=3)


class FeedbackReply(BaseModel):
    message: str


class ViewedItems(BaseModel):
    category: str
    revisions: list[dict]


class FeedbackResolution(BaseModel):
    outcome: str


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    publish: bool = True


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok"}


@app.get("/sdk/v1/loader.js")
def embed_loader() -> Response:
    source = ROOT / "packages" / "embed-sdk" / "loader.js"
    return Response(source.read_text(encoding="utf-8"), media_type="application/javascript", headers={
        "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
    })


@app.get("/embed/v1", response_class=HTMLResponse)
def embed_document(project: str, environment: str) -> HTMLResponse:
    manifest = load_project(project)
    if not manifest.data["environments"].get(environment, {}).get("embed_enabled"):
        raise HTTPException(404, "Embed is not enabled")
    ancestors = " ".join(manifest.data["allowed_embed_origins"][environment])
    html = "<!doctype html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Project tools</title><link rel='stylesheet' href='/assets/app.css'></head><body><div id='root'></div><script type='module' src='/assets/app.js'></script></body></html>"
    return HTMLResponse(html, headers={
        "Content-Security-Policy": f"default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; frame-ancestors {ancestors}; object-src 'none'; base-uri 'none'",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })


@app.get("/api/v1/embed/config")
def embed_config(project: str, environment: str) -> dict:
    manifest = load_project(project)
    if not manifest.data["environments"].get(environment, {}).get("embed_enabled"):
        raise HTTPException(404, "Embed is not enabled")
    return {"protocol": PROTOCOL, "project": project, "environment": environment,
            "allowed_origins": manifest.data["allowed_embed_origins"][environment]}


@app.post("/api/v1/embed/session/verify")
def verify_embed_session(payload: EmbedVerification) -> dict:
    manifest = load_project(payload.project)
    if payload.environment not in manifest.data["environments"]:
        raise HTTPException(404, "Unknown environment")
    claims = verify_host_token(payload.token, payload.nonce, payload.host_origin, manifest, app.state.settings, app.state.replay_store)
    if claims["environment"] != payload.environment:
        raise HTTPException(401, "Environment mismatch")
    return {"platform_token": create_platform_session(app.state.settings, claims), "expires_in": 900}


def embed_identity(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Platform session required")
    return verify_platform_session(app.state.settings, authorization.removeprefix("Bearer "))


def feedback_adapter(settings: Settings) -> JiraAdapter:
    if not settings.jira_base_url or not settings.jira_feedback_email or not settings.jira_feedback_token:
        raise HTTPException(503, "Feedback tracker is not configured")
    return JiraAdapter(JiraCredentials(settings.jira_base_url, settings.jira_feedback_email, settings.jira_feedback_token))


def unread_count(session: Session, identity: dict, category: str, items: list[dict]) -> int:
    count = 0
    for item in items:
        item_id = f"{category}:{item.get('key') or item.get('id') or item.get('revision')}"
        marker = session.scalar(select(UnreadMarker).where(
            UnreadMarker.project_id == identity["project"], UnreadMarker.environment == identity["environment"],
            UnreadMarker.opaque_user_id == identity["sub"], UnreadMarker.conversation_id == item_id,
        ))
        if not marker or marker.seen_revision != item.get("revision"):
            count += 1
    return count


@app.get("/api/v1/embed/feed")
async def embed_feed(identity: dict = Depends(embed_identity)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Operations feed is unavailable")
    manifest = load_project(identity["project"])
    feedback = await conversations(feedback_adapter(app.state.settings), manifest.data, identity)
    with Session(app.state.engine) as session:
        announcements = [{
            "id": value.id, "title": value.title, "body": value.body,
            "revision": value.published_at.isoformat() if value.published_at else value.created_at.isoformat(),
        } for value in session.scalars(select(Announcement).where(
            Announcement.project_id == identity["project"], Announcement.environment == identity["environment"],
            Announcement.published_at.is_not(None),
        ).order_by(Announcement.published_at.desc()).limit(50))]
        releases = [{
            "id": str(value.id), "revision": value.source_sha, "image_digest": value.image_digest,
            "status": value.status, "deployed_at": value.observed_at.isoformat(),
        } for value in session.scalars(select(DeploymentObservation).where(
            DeploymentObservation.project_id == identity["project"],
            DeploymentObservation.environment == identity["environment"],
            DeploymentObservation.status == "success",
        ).order_by(DeploymentObservation.observed_at.desc()).limit(50))]
        unread = {category: unread_count(session, identity, category, values) for category, values in (
            ("feedback", feedback), ("releases", releases), ("announcements", announcements),
        )}
    return {"feedback": feedback, "releases": releases, "announcements": announcements, "unread": unread}


@app.post("/api/v1/embed/feedback", status_code=202)
def submit_embed_feedback(payload: FeedbackSubmission, identity: dict = Depends(embed_identity), idempotency_key: str | None = Header(default=None)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Feedback queue is unavailable")
    if not idempotency_key or len(idempotency_key) > 200 or not payload.message.strip() or len(payload.message) > 10_000:
        raise HTTPException(400, "A valid idempotency key and feedback message are required")
    context = {key: payload.context.get(key) for key in ("route", "theme", "viewport", "release") if key in payload.context}
    key_hash = hashlib.sha256(f"{identity['project']}:{identity['sub']}:{idempotency_key}".encode()).hexdigest()
    with Session(app.state.engine) as session:
        existing = session.query(IdempotencyRecord).filter_by(
            project_id=identity["project"], operation="feedback.create", key_hash=key_hash
        ).one_or_none()
        if existing:
            return {"status": existing.status, "operation_id": existing.id}
        record = IdempotencyRecord(
            project_id=identity["project"], operation="feedback.create", key_hash=key_hash,
            status="pending", result={"message": payload.message.strip(), "context": context,
                                      "environment": identity["environment"], "opaque_user_id": identity["sub"],
                                      "opaque_org_id": identity["org"], "role": identity["role"],
                                      "intent": payload.intent, "attachment_count": payload.attachment_count},
        )
        session.add(record); session.commit(); session.refresh(record)
        return {"status": "pending", "operation_id": record.id}


@app.post("/api/v1/embed/feedback/{operation_id}/attachments", status_code=202)
async def attach_embed_feedback(
    operation_id: int, slot: int = Form(...), image: UploadFile = File(...), identity: dict = Depends(embed_identity),
) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Feedback attachment queue is unavailable")
    content_type = image.content_type or ""
    data = await image.read(5 * 1024 * 1024 + 1)
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Each image must be 5 MB or smaller")
    if content_type not in signatures or not signatures[content_type]:
        raise HTTPException(415, "Only valid PNG, JPEG, and WebP images are supported")
    with Session(app.state.engine) as session:
        operation = session.get(IdempotencyRecord, operation_id)
        pending = dict(operation.result or {}) if operation else {}
        if not operation or operation.project_id != identity["project"] or pending.get("opaque_user_id") != identity["sub"]:
            raise HTTPException(404, "Pending feedback was not found")
        declared = min(3, int(pending.get("attachment_count", 0)))
        if slot < 0 or slot >= declared:
            raise HTTPException(422, "Attachment slot is outside the declared range")
        existing = session.scalar(select(PendingAttachment).where(
            PendingAttachment.operation_id == operation_id, PendingAttachment.slot == slot,
        ))
        if existing:
            return {"status": existing.status, "attachment_id": existing.id}
        attachment = PendingAttachment(
            operation_id=operation_id, slot=slot, opaque_user_id=identity["sub"],
            filename=Path(image.filename or "feedback-image").name[:255], content_type=content_type, content=data,
            status="pending",
        )
        session.add(attachment); session.commit(); session.refresh(attachment)
    return {"status": "pending", "attachment_id": attachment.id}


@app.post("/api/v1/embed/feedback/{issue_key}/reply")
async def reply_to_feedback(issue_key: str, payload: FeedbackReply, identity: dict = Depends(embed_identity)) -> dict:
    message = payload.message.strip()
    if not message or len(message) > 10_000:
        raise HTTPException(422, "A reply of 1 to 10,000 characters is required")
    manifest = load_project(identity["project"])
    adapter = feedback_adapter(app.state.settings)
    allowed = {item["key"] for item in await conversations(adapter, manifest.data, identity)}
    if issue_key not in allowed:
        raise HTTPException(404, "Feedback not found")
    comment = await adapter.add_comment(issue_key, adf(f"[Launch LMS tester comment] {message}"), public=True)
    return {"status": "sent", "comment_id": comment.get("id")}


@app.get("/api/v1/embed/feedback/{issue_key}/attachments/{attachment_id}")
async def get_feedback_attachment(issue_key: str, attachment_id: str, identity: dict = Depends(embed_identity)) -> Response:
    manifest = load_project(identity["project"])
    adapter = feedback_adapter(app.state.settings)
    thread = next((item for item in await conversations(adapter, manifest.data, identity) if item["key"] == issue_key), None)
    attachment = next((item for item in (thread or {}).get("attachments", []) if str(item.get("id")) == attachment_id), None)
    if not attachment:
        raise HTTPException(404, "Attachment not found")
    content, content_type = await adapter.attachment(attachment_id)
    filename = Path(attachment.get("filename") or "feedback-attachment").name
    return Response(content, media_type=content_type, headers={
        "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{filename.replace(chr(34), "")}"',
    })


@app.post("/api/v1/embed/feedback/{issue_key}/resolution")
async def resolve_feedback(issue_key: str, payload: FeedbackResolution, identity: dict = Depends(embed_identity)) -> dict:
    if payload.outcome not in {"looks_good", "still_happening"}:
        raise HTTPException(422, "Unknown resolution outcome")
    manifest = load_project(identity["project"])
    adapter = feedback_adapter(app.state.settings)
    threads = {item["key"]: item for item in await conversations(adapter, manifest.data, identity)}
    if issue_key not in threads:
        raise HTTPException(404, "Feedback not found")
    issue = await adapter.get_issue(issue_key)
    labels = set(issue.details.get("labels") or [])
    if payload.outcome == "looks_good":
        labels.add("tester-confirmed")
        text = "Looks good now."
    else:
        labels.discard("tester-confirmed")
        text = "Still happening after completion."
    metadata = await adapter.property(issue_key, "launchlms.feedback") or {}
    metadata["tester_resolution"] = payload.outcome
    metadata["tester_resolution_revision"] = issue.revision
    await adapter.update_fields(issue_key, {"labels": sorted(labels)})
    await adapter.set_properties(issue_key, {"launchlms.feedback": metadata})
    await adapter.add_comment(issue_key, adf(f"[Launch LMS tester comment] {text}"), public=True)
    return {"status": "recorded", "outcome": payload.outcome}


@app.post("/api/v1/embed/viewed")
def mark_embed_viewed(payload: ViewedItems, identity: dict = Depends(embed_identity)) -> dict:
    if app.state.engine is None or payload.category not in {"feedback", "releases", "announcements"}:
        raise HTTPException(422, "Invalid viewed update")
    with Session(app.state.engine) as session:
        for value in payload.revisions[:100]:
            item_id = str(value.get("id", ""))[:128]
            revision = str(value.get("revision", ""))[:128]
            if not item_id or not revision:
                continue
            marker = session.scalar(select(UnreadMarker).where(
                UnreadMarker.project_id == identity["project"], UnreadMarker.environment == identity["environment"],
                UnreadMarker.opaque_user_id == identity["sub"],
                UnreadMarker.conversation_id == f"{payload.category}:{item_id}",
            )) or UnreadMarker(
                project_id=identity["project"], environment=identity["environment"], opaque_user_id=identity["sub"],
                conversation_id=f"{payload.category}:{item_id}", seen_revision=revision,
            )
            marker.seen_revision = revision
            session.add(marker)
        session.commit()
    return {"status": "recorded"}


@app.get("/api/v1/auth/github/login")
def github_login() -> RedirectResponse:
    url, nonce = login_url(app.state.settings)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "launch_operations_oauth", nonce, httponly=True,
        secure=app.state.settings.environment != "development", samesite="lax",
        max_age=600, path="/api/v1/auth/github/callback",
    )
    return response


@app.get("/api/v1/auth/github/callback")
async def github_callback(code: str, state: str, launch_operations_oauth: str | None = Cookie(default=None)) -> RedirectResponse:
    operator = await exchange_and_authorize(app.state.settings, code, state, launch_operations_oauth)
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("launch_operations_oauth", path="/api/v1/auth/github/callback")
    response.set_cookie(
        COOKIE,
        create_session(app.state.settings, operator),
        httponly=True,
        secure=app.state.settings.environment != "development",
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
    return response


@app.post("/api/v1/auth/logout", status_code=204, response_class=Response)
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/v1/operator")
def operator(identity: dict = Depends(require_operator)) -> dict:
    return identity


@app.get("/api/v1/projects/{project_id}")
def project(project_id: str, _: dict = Depends(require_operator)) -> dict:
    manifest = load_project(project_id)
    data = manifest.data
    return {
        "project_id": data["project_id"],
        "display_name": data["display_name"],
        "repository": data["source"]["repository"],
        "environments": data["environments"],
        "modules": data["modules"],
    }


@app.post("/api/v1/projects/{project_id}/announcements/{environment}", status_code=201)
def create_announcement(project_id: str, environment: str, payload: AnnouncementCreate, _: dict = Depends(require_operator)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Announcement storage is unavailable")
    manifest = load_project(project_id)
    if environment not in manifest.data["environments"]:
        raise HTTPException(404, "Unknown environment")
    with Session(app.state.engine) as session:
        value = Announcement(
            project_id=project_id, environment=environment, title=payload.title.strip(), body=payload.body.strip(),
            published_at=datetime.now(timezone.utc) if payload.publish else None,
        )
        session.add(value); session.commit(); session.refresh(value)
    return {"id": value.id, "published_at": value.published_at}


@app.post("/api/v1/projects/{project_id}/feedback/sync")
async def sync_feedback(project_id: str, _: dict = Depends(require_operator)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Feedback synchronization storage is unavailable")
    manifest = load_project(project_id)
    return await synchronize_pending(app.state.engine, feedback_adapter(app.state.settings), manifest.data)


def github_app(settings: Settings) -> GitHubAppCredentials:
    if not settings.github_app_id or not settings.github_installation_id or not settings.github_app_private_key:
        raise HTTPException(503, "GitHub App dispatch is not configured")
    return GitHubAppCredentials(settings.github_app_id, settings.github_installation_id, settings.github_app_private_key)


@app.post("/api/v1/projects/{project_id}/deployments/{environment}/dispatch", status_code=202)
async def dispatch_candidate(project_id: str, environment: str, payload: CandidateDispatch, _: dict = Depends(require_operator)) -> dict:
    manifest = load_project(project_id)
    target = manifest.data["environments"].get(environment)
    if not target or environment != "unstable" or not target.get("automatic"):
        raise HTTPException(403, "Only manifest-authorized automatic candidate dispatch is allowed")
    try:
        candidate = validate_candidate(payload.candidate, manifest.data)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    connector = await github_app(app.state.settings).connector()
    deployment = manifest.data["deployment"]
    await connector.repository_dispatch(deployment["repository"], "unstable-candidate", candidate)
    return {"status": "requested", "source_sha": candidate["source_sha"], "image_digest": candidate["image_digest"]}


@app.post("/api/v1/projects/{project_id}/deployments/{environment}/observe")
async def observe_deployment(project_id: str, environment: str, payload: DeploymentResult, _: dict = Depends(require_operator)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Deployment observation storage is unavailable")
    manifest = load_project(project_id)
    if environment not in manifest.data["environments"]:
        raise HTTPException(404, "Unknown environment")
    connector = await github_app(app.state.settings).connector()
    deployment = manifest.data["deployment"]
    run = await connector.workflow_run(deployment["repository"], payload.run_id)
    if run.get("path", "").split("@", 1)[0] != f".github/workflows/{deployment['workflow']}":
        raise HTTPException(409, "Observed run is not the registered deployment workflow")
    status = run.get("conclusion") or run.get("status", "unknown")
    with Session(app.state.engine) as session:
        record = DeploymentObservation(
            project_id=project_id, environment=environment, source_sha=payload.source_sha,
            image_digest=payload.image_digest, workflow_run_id=str(payload.run_id), status=status,
        )
        session.add(record); session.commit(); session.refresh(record)
    return {"status": status, "workflow_run_id": str(payload.run_id), "observed_at": record.observed_at}


@app.get("/api/v1/orchestration/status")
async def orchestration_status(_: dict = Depends(require_operator)) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(app.state.settings.symphony_status_url)
            response.raise_for_status()
            raw = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(503, "Orchestrator status is unavailable") from error
    allowed = ("status", "paused", "running", "queued", "updated_at")
    return {key: raw[key] for key in allowed if key in raw}
