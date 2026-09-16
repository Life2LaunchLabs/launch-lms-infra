"""Contracts for the reusable operations-platform foundation."""

from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect
import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.deployer.candidate import environment_lock, validate_candidate
from services.orchestrator.manifest import load_project, validate
from services.orchestrator.render_workflow import render


def import_api_module(name: str):
    api = ROOT / "apps" / "control-plane" / "api"
    sys.path.insert(0, str(api))
    spec = importlib.util.spec_from_file_location(name, api / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ManifestTests(unittest.TestCase):
    def test_launch_registration_is_complete(self):
        manifest = load_project("launch-lms")
        self.assertEqual(manifest.repository, "Life2LaunchLabs/launch-lms")
        self.assertEqual(manifest.workflow_path, "WORKFLOW.md")
        self.assertFalse(manifest.data["environments"]["production"]["embed_enabled"])
        self.assertEqual(manifest.data["tracker"]["statuses"]["approved"], "Merge")
        self.assertEqual(manifest.data["deployment"]["repository"], "Life2LaunchLabs/launch-lms-infra")

    def test_manifest_rejects_missing_lifecycle(self):
        data = load_project("launch-lms").data.copy()
        data["tracker"] = {**data["tracker"], "statuses": {"ready": "To Do"}}
        with self.assertRaisesRegex(ValueError, "complete lifecycle"):
            validate(data)

    def test_workflow_records_exact_product_and_rendered_hashes(self):
        commit = "a" * 40
        body, metadata = render("launch-lms", commit, b"# Product policy\n\nDo the work.\n")
        self.assertIn(f"product_commit={commit}", body)
        self.assertEqual(metadata["product_commit"], commit)
        self.assertEqual(len(metadata["rendered_workflow_sha256"]), 64)


class CandidateTests(unittest.TestCase):
    def candidate(self):
        return {
            "schema_version": 1,
            "source_sha": "b" * 40,
            "image": "ghcr.io/life2launchlabs/launch-lms",
            "image_digest": "sha256:" + "c" * 64,
            "architectures": ["amd64", "arm64"],
            "version": "sha-b",
            "migration": {"heads": ["head"]},
            "build_run_id": "123",
        }

    def test_lock_preserves_exact_digest(self):
        manifest = load_project("launch-lms").data
        candidate = validate_candidate(self.candidate(), manifest)
        lock = environment_lock(candidate, "unstable")
        self.assertEqual(lock["image_digest"], candidate["image_digest"])
        self.assertEqual(lock["source_sha"], candidate["source_sha"])

    def test_rejects_mutable_digest(self):
        candidate = self.candidate()
        candidate["image_digest"] = "latest"
        with self.assertRaisesRegex(ValueError, "immutable"):
            validate_candidate(candidate, load_project("launch-lms").data)


class ConnectorTests(unittest.TestCase):
    def test_jira_adapter_brokers_issue_properties_without_secret_repr(self):
        from packages.connectors.jira import JiraAdapter, JiraCredentials
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == "POST" and request.url.path == "/rest/api/3/issue":
                return httpx.Response(201, json={"key": "FEED-1"})
            if request.method == "PUT":
                return httpx.Response(204)
            return httpx.Response(200, json={
                "key": "FEED-1", "fields": {"summary": "Feedback", "status": {"name": "Open"}, "updated": "r1"},
                "properties": {"launch-operations": {"environment": "unstable"}},
            })

        credentials = JiraCredentials("https://jira.example", "feedback@example.test", "secret-token")
        self.assertNotIn("secret-token", repr(credentials))
        adapter = JiraAdapter(credentials, httpx.MockTransport(handler))
        issue = asyncio.run(adapter.create_issue(
            "FEED", "Feedback", {"type": "doc", "version": 1, "content": []},
            {"launch-operations": {"environment": "unstable"}}, "operation-1",
        ))
        self.assertEqual(issue.key, "FEED-1")
        self.assertEqual([request.method for request in requests], ["POST", "PUT", "GET"])
        self.assertTrue(all("secret-token" not in str(request.content) for request in requests))

    def test_github_app_mints_short_lived_installation_capability(self):
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
        from packages.connectors.github import GitHubAppCredentials

        private = generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path.endswith("/access_tokens"):
                assertion = request.headers["authorization"].removeprefix("Bearer ")
                claims = jwt.decode(assertion, private.public_key(), algorithms=["RS256"], options={"verify_aud": False})
                self.assertEqual(claims["iss"], "123")
                return httpx.Response(201, json={
                    "token": "installation-secret",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat(),
                })
            if request.method == "POST":
                self.assertEqual(request.headers["authorization"], "Bearer installation-secret")
                return httpx.Response(204)
            return httpx.Response(200, json={"id": 55, "status": "completed", "conclusion": "success"})

        credentials = GitHubAppCredentials("123", "456", pem)
        self.assertNotIn("PRIVATE KEY", repr(credentials))

        async def exercise():
            connector = await credentials.connector("https://api.github.test", httpx.MockTransport(handler))
            self.assertNotIn("installation-secret", repr(connector))
            await connector.dispatch("owner/repo", "deploy.yaml", "main", {"environment": "unstable"})
            return await connector.workflow_run("owner/repo", 55)

        self.assertEqual(asyncio.run(exercise())["conclusion"], "success")
        self.assertEqual(len(seen), 3)


class OperationalSchemaTests(unittest.TestCase):
    def test_all_operational_tables_create_without_tracker_content_table(self):
        models = import_api_module("models")
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        self.assertEqual(
            set(inspect(engine).get_table_names()),
            {"projects", "embed_sessions", "unread_markers", "sync_cursors", "idempotency_records", "agent_runs", "deployment_observations", "announcements"},
        )


class OperatorAuthTests(unittest.TestCase):
    def test_session_is_signed_and_time_limited(self):
        auth = import_api_module("auth")
        config = import_api_module("config")
        settings = config.Settings(
            github_client_id="client", github_client_secret="secret",
            session_secret="x" * 32, public_url="https://ops.example.test",
        )
        token = auth.create_session(settings, {"id": "1", "login": "operator"})
        identity = auth.serializer(settings, "operator-session").loads(token, max_age=60)
        self.assertEqual(identity["login"], "operator")
        self.assertNotIn("github_token", identity)

    def test_login_state_is_bound_to_a_browser_nonce(self):
        auth = import_api_module("auth")
        config = import_api_module("config")
        settings = config.Settings(
            github_client_id="client", github_client_secret="secret",
            session_secret="x" * 32, public_url="https://ops.example.test",
        )
        url, nonce = auth.login_url(settings)
        self.assertIn("state=", url)
        self.assertGreater(len(nonce), 20)

    def test_control_plane_application_imports_with_public_health_route(self):
        main = import_api_module("main")
        self.assertIn("/healthz", {route.path for route in main.app.routes})


class RunnerBrokerTests(unittest.TestCase):
    def setUp(self):
        self.main = import_api_module("main")
        self.models = sys.modules["models"]
        self.key = "runner-broker-key-that-is-long-enough"
        self.main.app.state.settings = self.main.Settings(runner_broker_key=self.key)

    def test_installation_token_requires_broker_key_and_preserves_expiry(self):
        class Connector:
            token = "short-lived-installation-token"
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=50)

        class Credentials:
            async def connector(self):
                return Connector()

        with self.assertRaisesRegex(Exception, "Runner broker authorization failed"):
            asyncio.run(self.main.runner_installation_token(None))
        with patch.object(self.main, "github_app", return_value=Credentials()):
            result = asyncio.run(self.main.runner_installation_token(self.key))
        self.assertEqual(result["token"], "short-lived-installation-token")
        self.assertIn("+00:00", result["expires_at"])

    def test_review_record_is_idempotently_upserted(self):
        engine = create_engine("sqlite:///:memory:")
        self.models.Base.metadata.create_all(engine)
        self.main.app.state.engine = engine
        with self.main.Session(engine) as session:
            session.add(self.models.Project(id="launch-lms", manifest_revision="r" * 64, enabled=True))
            session.commit()
        payload = self.main.RunnerReviewRecord(
            project_id="launch-lms", issue_key="BOT-253", attempt=1, workspace="BOT-253",
            policy_revision="a" * 40, workflow_hash="b" * 64, base_sha="c" * 40,
            head_sha="d" * 40, turns=4, duration_ms=1234, checks={"unit": "passed"},
            evidence={"files": ["review.md"]},
        )
        first = self.main.record_runner_review(payload, self.key)
        second = self.main.record_runner_review(payload, self.key)
        self.assertEqual(first["run_id"], second["run_id"])
        with self.main.Session(engine) as session:
            self.assertEqual(session.query(self.models.AgentRun).count(), 1)


if __name__ == "__main__":
    unittest.main()
