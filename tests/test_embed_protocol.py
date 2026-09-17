"""Security and transport contracts for the cross-origin embed protocol."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/control-plane/api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API))

from services.orchestrator.manifest import ProjectManifest, load_project


import models
import embed


class EmbedProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.current = Ed25519PrivateKey.generate()
        self.next = Ed25519PrivateKey.generate()
        data = deepcopy(load_project("launch-lms").data)
        keys = {}
        for key_id, private in (
            (data["token_verification"]["current_key_id"], self.current),
            (data["token_verification"]["next_key_id"], self.next),
        ):
            path = root / f"{key_id}.pem"
            path.write_bytes(private.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ))
            keys[key_id] = path.name
        data["token_verification"]["public_keys"] = keys
        self.manifest = ProjectManifest(root / "project.yaml", data)
        self.engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True))
            session.commit()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.nonce = "8bc4e4f2-073f-4eb7-86c4-82d4e96906b6"

    def tearDown(self):
        self.temp.cleanup()

    def token(self, private=None, key_id=None, **overrides):
        policy = self.manifest.data["token_verification"]
        private = private or self.current
        key_id = key_id or policy["current_key_id"]
        claims = {
            "iss": policy["issuer"], "aud": policy["audience"],
            "project": "launch-lms", "environment": "unstable",
            "sub": "a" * 64, "org": "b" * 64, "role": "Learner",
            "nonce": self.nonce, "iat": int(self.now.timestamp()),
            "exp": int((self.now + timedelta(minutes=4)).timestamp()),
        }
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="EdDSA", headers={"kid": key_id})

    def payload(self, **overrides):
        data = {"nonce": self.nonce, "protocol": embed.PROTOCOL,
                "parent_origin": "https://unstable.life2launch.app"}
        data.update(overrides)
        return embed.EmbedSessionRequest(**data)

    def test_current_and_next_keys_accept_exact_and_tenant_origins(self):
        current = embed.verify_token(self.token(), self.payload(), self.manifest, self.now)
        rotated = embed.verify_token(
            self.token(self.next, self.manifest.data["token_verification"]["next_key_id"]),
            self.payload(parent_origin="https://school.unstable.life2launch.app"),
            self.manifest, self.now,
        )
        self.assertEqual(current["sub"], "a" * 64)
        self.assertEqual(rotated["org"], "b" * 64)

    def test_rejects_expired_future_wrong_audience_origin_and_nonce(self):
        cases = (
            (self.token(exp=int((self.now - timedelta(seconds=1)).timestamp())), self.payload()),
            (self.token(iat=int((self.now + timedelta(minutes=1)).timestamp()),
                        exp=int((self.now + timedelta(minutes=2)).timestamp())), self.payload()),
            (self.token(aud="someone-else"), self.payload()),
            (self.token(key_id="unknown-key"), self.payload()),
            (self.token(environment="production"), self.payload()),
            (self.token(debug="must-not-cross-boundary"), self.payload()),
            (self.token(), self.payload(parent_origin="https://evil.example")),
            (self.token(), self.payload(parent_origin="https://evilunstable.life2launch.app")),
            (self.token(), self.payload(parent_origin="https://deep.school.unstable.life2launch.app")),
            (self.token(), self.payload(nonce="00000000-0000-0000-0000-000000000000")),
        )
        for token, payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                embed.verify_token(token, payload, self.manifest, self.now)

    def test_redeem_persists_no_token_and_rejects_replay(self):
        with Session(self.engine) as session:
            record, credential = embed.redeem(self.token(), self.payload(), session, self.manifest, self.now)
            self.assertEqual(record.parent_origin, "https://unstable.life2launch.app")
            self.assertEqual(record.role, "Learner")
            self.assertFalse(hasattr(record, "token"))
            self.assertNotEqual(record.credential_hash, credential)
            authenticated = embed.require_embed_session(f"Session {credential}", session)
            self.assertEqual(authenticated.id, record.id)
            record.expires_at = self.now - timedelta(seconds=1)
            session.add(record); session.commit()
            with self.assertRaises(HTTPException) as expired:
                embed.require_embed_session(f"Session {credential}", session)
            self.assertEqual(expired.exception.status_code, 401)
        with Session(self.engine) as session, self.assertRaisesRegex(ValueError, "already been used"):
            embed.redeem(self.token(), self.payload(), session, self.manifest, self.now)

    def test_loader_never_places_session_token_in_url_or_storage(self):
        loader = (ROOT / "packages/embed-sdk/loader.js").read_text()
        iframe = (ROOT / "apps/control-plane/web/public/embed/v1/embed.js").read_text()
        self.assertIn("new MessageChannel()", loader)
        self.assertIn("[channel.port2]", loader)
        self.assertIn("issueSession(nonce()), 180000", loader)
        self.assertNotIn("searchParams.set('token'", loader)
        self.assertNotIn("localStorage", loader + iframe)
        self.assertNotIn("sessionStorage", loader + iframe)
        self.assertIn("Authorization: `Bearer ${token}`", iframe)
        self.assertIn("Authorization: `Session ${platformSession}`", iframe)
        self.assertIn("/api/v1/embed/feedback", iframe)
        self.assertIn("'Idempotency-Key'", iframe)
        self.assertIn("platformSession = redeemed.session_token", iframe)
        self.assertIn("event.origin !== parentOrigin", iframe)
        self.assertIn("nonce: activeNonce", loader)

    def test_csp_distinguishes_operator_app_sdk_and_embed(self):
        caddy = (ROOT / "deploy/control-plane/Caddyfile").read_text()
        manifest = load_project("launch-lms").data
        self.assertIn("@embed path /embed/v1/*", caddy)
        for origin in manifest["allowed_embed_origins"]["unstable"]:
            self.assertIn(origin, caddy)
        self.assertIn('Access-Control-Allow-Origin "*"', caddy)
        self.assertIn("frame-ancestors 'none'", caddy)


if __name__ == "__main__":
    unittest.main()
