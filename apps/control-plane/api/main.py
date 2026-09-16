"""Authenticated operations control-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import sys

import httpx
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
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
from models import AgentRun, DeploymentObservation, Project  # noqa: E402


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


class CandidateDispatch(BaseModel):
    candidate: dict


class DeploymentResult(BaseModel):
    run_id: int
    source_sha: str
    image_digest: str


class RunnerReviewRecord(BaseModel):
    project_id: str
    issue_key: str
    attempt: int
    workspace: str
    policy_revision: str
    workflow_hash: str
    base_sha: str
    head_sha: str
    turns: int
    duration_ms: int
    checks: dict
    evidence: dict
    disposition: str = "in_review"


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


def authorize_runner(x_runner_key: str | None) -> None:
    expected = app.state.settings.runner_broker_key
    if len(expected) < 32 or not x_runner_key or not hmac.compare_digest(expected, x_runner_key):
        raise HTTPException(401, "Runner broker authorization failed")


@app.post("/internal/v1/github/installation-token")
async def runner_installation_token(x_runner_key: str | None = Header(default=None)) -> dict:
    authorize_runner(x_runner_key)
    connector = await github_app(app.state.settings).connector()
    return {"token": connector.token, "expires_at": connector.expires_at.isoformat()}


@app.post("/internal/v1/runs/review", status_code=202)
def record_runner_review(payload: RunnerReviewRecord, x_runner_key: str | None = Header(default=None)) -> dict:
    authorize_runner(x_runner_key)
    if app.state.engine is None:
        raise HTTPException(503, "Run storage is unavailable")
    if payload.workspace != payload.issue_key or payload.attempt < 0:
        raise HTTPException(422, "Invalid workspace or attempt")
    if any(len(value) != size for value, size in (
        (payload.policy_revision, 40), (payload.workflow_hash, 64),
        (payload.base_sha, 40), (payload.head_sha, 40),
    )):
        raise HTTPException(422, "Run revisions must be full hashes")
    manifest = load_project(payload.project_id)
    if not payload.issue_key.startswith(manifest.data["tracker"]["delivery_project"] + "-"):
        raise HTTPException(422, "Issue does not belong to the registered project")
    finished_at = datetime.now(timezone.utc)
    with Session(app.state.engine) as session:
        record = session.query(AgentRun).filter_by(
            project_id=payload.project_id, issue_key=payload.issue_key, attempt=payload.attempt,
        ).one_or_none()
        values = payload.model_dump(exclude={"project_id", "issue_key", "attempt"})
        if record is None:
            record = AgentRun(
                project_id=payload.project_id, issue_key=payload.issue_key, attempt=payload.attempt,
                started_at=finished_at - timedelta(milliseconds=payload.duration_ms), **values,
            )
            session.add(record)
        else:
            for field, value in values.items():
                setattr(record, field, value)
        record.finished_at = finished_at
        session.commit()
        session.refresh(record)
        run_id = record.id
    return {"status": "recorded", "run_id": run_id}


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
