"""GitHub OAuth operator gate; there is no public account registration."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from urllib.parse import urlencode

from fastapi import Cookie, HTTPException, Request
import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import Settings


COOKIE = "launch_operations_session"


def serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    settings.require_auth()
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def login_url(settings: Settings) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(24)
    state = serializer(settings, "github-oauth-state").dumps({"nonce": nonce})
    query = urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": settings.public_url.rstrip("/") + "/api/v1/auth/github/callback",
        "scope": "read:user read:org",
        "state": state,
    })
    return "https://github.com/login/oauth/authorize?" + query, nonce


async def exchange_and_authorize(settings: Settings, code: str, state: str, expected_nonce: str | None) -> dict:
    try:
        state_data = serializer(settings, "github-oauth-state").loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(400, "Invalid or expired OAuth state") from error
    if not expected_nonce or not secrets.compare_digest(state_data["nonce"], expected_nonce):
        raise HTTPException(400, "OAuth state does not match this browser")
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret, "code": code},
        )
        token_response.raise_for_status()
        token = token_response.json().get("access_token")
        if not token:
            raise HTTPException(401, "GitHub authorization failed")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        user_response = await client.get("https://api.github.com/user", headers=headers)
        user_response.raise_for_status()
        user = user_response.json()
        membership = await client.get(f"https://api.github.com/user/memberships/orgs/{settings.github_org}", headers=headers)
        organization_allowed = membership.status_code == 200 and membership.json().get("state") == "active"
        repository = await client.get(f"https://api.github.com/repos/{settings.github_repo}", headers=headers)
        permissions = repository.json().get("permissions", {}) if repository.status_code == 200 else {}
        collaborator_allowed = any(permissions.get(name) for name in ("admin", "maintain", "push", "triage"))
        if not organization_allowed and not collaborator_allowed:
            raise HTTPException(403, "Operator access requires approved organization membership or repository collaboration")
        return {"id": str(user["id"]), "login": user["login"], "avatar_url": user.get("avatar_url")}


def create_session(settings: Settings, operator: dict) -> str:
    return serializer(settings, "operator-session").dumps({
        **operator, "issued_at": datetime.now(timezone.utc).isoformat()
    })


def require_operator(request: Request, launch_operations_session: str | None = Cookie(default=None, alias=COOKIE)) -> dict:
    if not launch_operations_session:
        raise HTTPException(401, "Operator login required")
    settings: Settings = request.app.state.settings
    try:
        return serializer(settings, "operator-session").loads(launch_operations_session, max_age=12 * 60 * 60)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(401, "Operator session expired") from error
