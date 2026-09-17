"""Authenticated operations control-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
import sys

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.orchestrator.manifest import load_project  # noqa: E402
from services.deployer.candidate import validate_candidate  # noqa: E402
from packages.connectors.github import GitHubAppCredentials  # noqa: E402
from auth import COOKIE, create_session, exchange_and_authorize, login_url, require_operator  # noqa: E402
from config import Settings  # noqa: E402
from models import DeploymentObservation, Project  # noqa: E402
from embed import router as embed_router  # noqa: E402
from feedback import router as feedback_router  # noqa: E402
from announcements import router as announcements_router  # noqa: E402
from orchestration import sanitized_status  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings()
    if app.state.settings.environment == "development":
        app.state.engine = None
    else:
        engine = create_engine(app.state.settings.database_url, pool_pre_ping=True)
        manifest = load_project("launch-lms")
        revision = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
        with Session(engine) as session:
            session.merge(Project(id=manifest.project_id, manifest_revision=revision, enabled=True))
            session.commit()
        app.state.engine = engine
    yield


app = FastAPI(title="Launch Operations", version="0.1.0", lifespan=lifespan)
app.include_router(embed_router)
app.include_router(feedback_router)
app.include_router(announcements_router)


class ReadOnlyMiddleware:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["app"].state.settings.read_only
                and scope["path"].startswith("/api/v1/")
                and scope["method"] not in {"GET", "HEAD", "OPTIONS"}
                and scope["path"] != "/api/v1/auth/logout"):
            await JSONResponse({"detail": "Operations are read-only"}, status_code=403)(scope, receive, send)
            return
        await self.wrapped(scope, receive, send)


app.add_middleware(ReadOnlyMiddleware)


class CandidateDispatch(BaseModel):
    candidate: dict


class DeploymentResult(BaseModel):
    run_id: int
    source_sha: str
    image_digest: str


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok"}


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
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
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
    deployment = manifest.data["deployment"]
    connector = await github_app(app.state.settings).connector(deployment["repository"], {"contents": "write"})
    await connector.repository_dispatch(deployment["repository"], "unstable-candidate", candidate)
    return {"status": "requested", "source_sha": candidate["source_sha"], "image_digest": candidate["image_digest"]}


@app.post("/api/v1/projects/{project_id}/deployments/{environment}/observe")
async def observe_deployment(project_id: str, environment: str, payload: DeploymentResult, _: dict = Depends(require_operator)) -> dict:
    if app.state.engine is None:
        raise HTTPException(503, "Deployment observation storage is unavailable")
    manifest = load_project(project_id)
    if environment not in manifest.data["environments"]:
        raise HTTPException(404, "Unknown environment")
    deployment = manifest.data["deployment"]
    connector = await github_app(app.state.settings).connector(deployment["repository"], {"actions": "read"})
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
    try:
        return sanitized_status(raw)
    except ValueError as error:
        raise HTTPException(503, "Orchestrator status is unavailable") from error
