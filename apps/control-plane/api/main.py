"""Authenticated operations control-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import sys

import httpx
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(os.getenv("OPERATIONS_ROOT", str(Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(ROOT))

from services.orchestrator.manifest import load_project  # noqa: E402
from services.deployer.candidate import validate_candidate  # noqa: E402
from packages.connectors.github import GitHubAppCredentials  # noqa: E402
from auth import COOKIE, create_session, exchange_and_authorize, login_url, require_operator  # noqa: E402
from config import Settings  # noqa: E402
from embed_auth import (DatabaseReplayStore, MemoryReplayStore, PROTOCOL, create_platform_session,
                        origin_allowed, verify_host_token, verify_platform_session)  # noqa: E402
from models import DeploymentObservation, IdempotencyRecord, Project  # noqa: E402


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


@app.get("/api/v1/embed/feed")
def embed_feed(_: dict = Depends(embed_identity)) -> dict:
    return {"feedback": [], "releases": [], "announcements": [],
            "unread": {"feedback": 0, "releases": 0, "announcements": 0}}


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
                                      "opaque_org_id": identity["org"], "role": identity["role"]},
        )
        session.add(record); session.commit(); session.refresh(record)
        return {"status": "pending", "operation_id": record.id}


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
