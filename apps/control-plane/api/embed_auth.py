"""Ed25519 host-token verification and short-lived platform sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Protocol

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired
import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import serializer
from config import Settings
from services.orchestrator.manifest import ProjectManifest
from models import EmbedSession


PROTOCOL = "launch-operations/v1"
REQUIRED = {"project", "environment", "sub", "org", "role", "nonce", "iat", "exp", "aud", "iss"}


def origin_allowed(origin: str, allowed: list[str]) -> bool:
    for pattern in allowed:
        if origin == pattern:
            return True
        if "*." in pattern:
            prefix, domain = pattern.split("*.", 1)
            suffix = "." + domain
            if origin.startswith(prefix) and origin.endswith(suffix) and re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", origin[len(prefix):-len(suffix)]
            ):
                return True
    return False


class ReplayStore(Protocol):
    def consume(self, project: str, environment: str, user: str, org: str, nonce_hash: str, expires_at: datetime) -> bool: ...


class MemoryReplayStore:
    def __init__(self):
        self.nonces: dict[str, datetime] = {}

    def consume(self, project: str, environment: str, user: str, org: str, nonce_hash: str, expires_at: datetime) -> bool:
        now = datetime.now(timezone.utc)
        self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry > now}
        if nonce_hash in self.nonces:
            return False
        self.nonces[nonce_hash] = expires_at
        return True


class DatabaseReplayStore:
    def __init__(self, engine):
        self.engine = engine

    def consume(self, project: str, environment: str, user: str, org: str, nonce_hash: str, expires_at: datetime) -> bool:
        try:
            with Session(self.engine) as session:
                session.add(EmbedSession(
                    project_id=project, environment=environment, opaque_user_id=user,
                    opaque_org_id=org, nonce_hash=nonce_hash, expires_at=expires_at,
                ))
                session.commit()
            return True
        except IntegrityError:
            return False


def public_keys(settings: Settings) -> dict[str, str]:
    try:
        value = json.loads(settings.embed_public_keys_json)
    except json.JSONDecodeError as error:
        raise RuntimeError("EMBED_PUBLIC_KEYS_JSON must be valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("EMBED_PUBLIC_KEYS_JSON must map key ids to public PEM values")
    return value


def verify_host_token(token: str, nonce: str, host_origin: str, manifest: ProjectManifest, settings: Settings, replay: ReplayStore) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Invalid session token") from error
    key_ids = manifest.data["token_verification"]
    if header.get("alg") != "EdDSA" or header.get("kid") not in {key_ids["current_key_id"], key_ids["next_key_id"]}:
        raise HTTPException(401, "Unsupported session signing key")
    key = public_keys(settings).get(header["kid"])
    if not key:
        raise HTTPException(401, "Session signing key is not active")
    try:
        claims = jwt.decode(
            token, key, algorithms=["EdDSA"], audience=key_ids["audience"], issuer=key_ids["issuer"],
            options={"require": sorted(REQUIRED)}, leeway=5,
        )
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Invalid or expired session token") from error
    now = datetime.now(timezone.utc).timestamp()
    if claims["iat"] > now + 5 or claims["exp"] - claims["iat"] > 300:
        raise HTTPException(401, "Session token time window is invalid")
    if claims["project"] != manifest.project_id or claims["environment"] not in manifest.data["environments"]:
        raise HTTPException(401, "Session token project or environment mismatch")
    if claims["nonce"] != nonce or not re.fullmatch(r"[0-9a-f-]{16,64}", nonce):
        raise HTTPException(401, "Session token nonce mismatch")
    if not origin_allowed(host_origin, manifest.data["allowed_embed_origins"][claims["environment"]]):
        raise HTTPException(403, "Host origin is not allowed")
    nonce_hash = hashlib.sha256(f"{manifest.project_id}:{nonce}".encode()).hexdigest()
    if not replay.consume(manifest.project_id, claims["environment"], claims["sub"], claims["org"], nonce_hash, datetime.fromtimestamp(claims["exp"], timezone.utc)):
        raise HTTPException(409, "Session token nonce was already used")
    return claims


def create_platform_session(settings: Settings, claims: dict) -> str:
    return serializer(settings, "embed-platform-session").dumps({
        key: claims[key] for key in ("project", "environment", "sub", "org", "role", "nonce")
    })


def verify_platform_session(settings: Settings, token: str) -> dict:
    try:
        return serializer(settings, "embed-platform-session").loads(token, max_age=15 * 60)
    except (BadSignature, SignatureExpired) as error:
        raise HTTPException(401, "Platform session expired") from error
