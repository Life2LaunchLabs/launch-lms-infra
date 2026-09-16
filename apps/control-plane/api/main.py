"""Authenticated operations control-plane API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import RedirectResponse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from services.orchestrator.manifest import load_project  # noqa: E402
from auth import COOKIE, create_session, exchange_and_authorize, login_url, require_operator  # noqa: E402
from config import Settings  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings()
    yield


app = FastAPI(title="Launch Operations", version="0.1.0", lifespan=lifespan)


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
