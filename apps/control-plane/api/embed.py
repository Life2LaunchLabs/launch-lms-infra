"""Protocol-v1 session redemption for the cross-origin operations iframe."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
import jwt
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import EmbedSession
from services.orchestrator.manifest import ProjectManifest, load_project


router = APIRouter()
PROTOCOL = "launch-operations/v1"
NONCE = re.compile(r"^[0-9a-f-]{16,64}$")
OPAQUE_ID = re.compile(r"^[0-9a-f]{64}$")
CLAIMS = {"iss", "aud", "project", "environment", "sub", "org", "role", "nonce", "iat", "exp"}


class EmbedSessionRequest(BaseModel):
    nonce: str
    protocol: str
    parent_origin: str


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("invalid origin")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("invalid origin")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("non-local origins must use HTTPS")
    return parsed.scheme, parsed.hostname.lower(), parsed.port


def origin_allowed(origin: str, patterns: list[str]) -> bool:
    try:
        candidate = _origin(origin)
    except ValueError:
        return False
    for pattern in patterns:
        try:
            expected = _origin(pattern.replace("*.", "wildcard.", 1))
        except ValueError:
            continue
        if "*." not in pattern:
            if candidate == expected:
                return True
            continue
        suffix = expected[1].removeprefix("wildcard.")
        if not candidate[1].endswith("." + suffix):
            continue
        prefix = candidate[1].removesuffix("." + suffix)
        if candidate[0] == expected[0] and candidate[2] == expected[2] and prefix and "." not in prefix:
            return True
    return False


def verify_token(token: str, payload: EmbedSessionRequest, manifest: ProjectManifest,
                 now: datetime | None = None) -> dict:
    if payload.protocol != PROTOCOL or not NONCE.fullmatch(payload.nonce):
        raise ValueError("invalid embed protocol or nonce")
    token_policy = manifest.data["token_verification"]
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise ValueError("invalid session token") from error
    kid = header.get("kid")
    if header.get("alg") != "EdDSA" or kid not in token_policy["public_keys"]:
        raise ValueError("untrusted session signing key")
    key_path = (manifest.path.parent / token_policy["public_keys"][kid]).resolve()
    try:
        claims = jwt.decode(
            token, key_path.read_text(encoding="utf-8"), algorithms=["EdDSA"],
            audience=token_policy["audience"], issuer=token_policy["issuer"],
            options={"require": sorted(CLAIMS)},
        )
    except (OSError, jwt.PyJWTError) as error:
        raise ValueError("invalid or expired session token") from error
    if set(claims) != CLAIMS:
        raise ValueError("session token contains unsupported claims")
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if not isinstance(claims["iat"], int) or not isinstance(claims["exp"], int):
        raise ValueError("session token timestamps are invalid")
    if claims["iat"] > current + 15 or claims["exp"] <= current or not 0 < claims["exp"] - claims["iat"] <= 300:
        raise ValueError("session token lifetime is invalid")
    if claims["project"] != manifest.project_id or claims["nonce"] != payload.nonce:
        raise ValueError("session token is bound to a different request")
    environment = claims["environment"]
    environment_policy = manifest.data["environments"].get(environment)
    origins = manifest.data["allowed_embed_origins"].get(environment, [])
    if not environment_policy or not environment_policy.get("embed_enabled"):
        raise ValueError("embed is disabled for this environment")
    if not origin_allowed(payload.parent_origin, origins):
        raise ValueError("parent origin is not allowed")
    if not OPAQUE_ID.fullmatch(claims["sub"]) or not OPAQUE_ID.fullmatch(claims["org"]):
        raise ValueError("session subjects must be opaque")
    if not isinstance(claims["role"], str) or not 0 < len(claims["role"]) <= 64:
        raise ValueError("session role is invalid")
    return claims


def redeem(token: str, payload: EmbedSessionRequest, session: Session,
           manifest: ProjectManifest | None = None, now: datetime | None = None) -> EmbedSession:
    project = manifest or load_project("launch-lms")
    claims = verify_token(token, payload, project, now=now)
    record = EmbedSession(
        project_id=claims["project"], environment=claims["environment"],
        opaque_user_id=claims["sub"], opaque_org_id=claims["org"], role=claims["role"],
        parent_origin=payload.parent_origin,
        nonce_hash=hashlib.sha256(payload.nonce.encode()).hexdigest(),
        expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ValueError("session token nonce has already been used") from error
    session.refresh(record)
    return record


def database_session(request: Request):
    if request.app.state.engine is None:
        raise HTTPException(503, "Embed session storage is unavailable")
    with Session(request.app.state.engine) as session:
        yield session


@router.post("/api/v1/embed/session")
def create_embed_session(
    payload: EmbedSessionRequest,
    response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(database_session),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Embed session token required")
    token = authorization.removeprefix("Bearer ")
    if not token or len(token) > 8192:
        raise HTTPException(401, "Embed session token is invalid")
    try:
        record = redeem(token, payload, session)
    except ValueError as error:
        detail = str(error)
        status = 409 if "already been used" in detail else 401
        raise HTTPException(status, detail) from error
    response.headers["Cache-Control"] = "no-store"
    return {
        "session_id": record.id, "project": record.project_id,
        "environment": record.environment, "role": record.role,
        "expires_at": record.expires_at.isoformat(), "protocol": PROTOCOL,
    }
