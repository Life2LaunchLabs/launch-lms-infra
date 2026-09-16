"""Contracts for the reusable operations-platform foundation."""

from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
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
        self.assertEqual(manifest.data["source"]["policy_paths"]["design"], "docs/design/README.md")

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
    def test_github_text_file_accepts_wrapped_base64_from_contents_api(self):
        from packages.connectors.github import GitHubConnector

        async def handler(request):
            self.assertEqual(request.url.params["ref"], "a" * 40)
            return httpx.Response(200, json={
                "type": "file", "encoding": "base64", "sha": "b" * 40,
                "content": "aGVsbG8g\nd29ybGQ=\n",
            })

        connector = GitHubConnector("secret", api_url="https://github.test", transport=httpx.MockTransport(handler))
        content, blob = asyncio.run(connector.text_file("owner/repo", "docs/product/map/01-test.json", "a" * 40))
        self.assertEqual(content, "hello world")
        self.assertEqual(blob, "b" * 40)

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


class RepositoryPlanningTests(unittest.TestCase):
    def service(self):
        from services.planning.repository import RepositoryPlanning
        manifest = {
            "schema_version": 1, "source": "productOS/product.db", "source_exported_at": "2026-09-16T00:00:00Z",
            "expected_counts": {"groups": 1, "goals": 1, "activities": 1, "steps": 1},
        }
        group = {"id": "DISCOVERY", "title": "Discover", "intent": "Find things", "goals": [{
            "id": "DISCOVERY-G001", "title": "Find", "outcome": "Found", "activities": [{
                "id": "DISCOVERY-G001-A001", "title": "Search", "steps": [{
                    "id": "DISCOVERY-G001-A001-S001", "title": "Query",
                }],
            }],
        }]}

        class Connector:
            def __init__(self):
                self.calls = []
            async def branch_head(self, repository, branch):
                return "a" * 40
            async def contents(self, repository, path, ref):
                return [{"type": "file", "name": "01-discovery.json", "path": "docs/product/map/01-discovery.json"}]
            async def text_file(self, repository, path, ref):
                if path.endswith("manifest.json"):
                    return json.dumps(manifest), "c" * 40
                if path.endswith("README.md"):
                    return "# Design index\n", "d" * 40
                return json.dumps(group), "b" * 40
            async def create_branch(self, repository, branch, source_sha):
                self.calls.append(("branch", branch, source_sha))
            async def update_file(self, repository, path, branch, content, blob_sha, message):
                self.calls.append(("update", path, branch, blob_sha))
                return {"commit": {"sha": "e" * 40}}
            async def create_pull_request(self, repository, title, body, head, base):
                self.calls.append(("pr", head, base))
                return {"number": 75, "html_url": "https://github.test/pull/75"}

        connector = Connector()
        return RepositoryPlanning(connector, load_project("launch-lms")), connector, group

    def test_catalog_is_bound_to_one_revision_and_reports_hierarchy(self):
        service, _, _ = self.service()
        catalog = asyncio.run(service.catalog())
        self.assertEqual(catalog["source_revision"], "a" * 40)
        self.assertEqual(catalog["groups"][0]["step_count"], 1)
        self.assertEqual(catalog["migration"]["expected_counts"]["activities"], 1)

    def test_edit_creates_new_branch_and_pull_request(self):
        service, connector, group = self.service()
        result = asyncio.run(service.propose_edit(
            path="docs/product/map/01-discovery.json", content=json.dumps(group),
            expected_blob_sha="b" * 40, base_sha="a" * 40, title="Clarify discovery goal",
            reason="Make the durable outcome clearer.",
        ))
        self.assertEqual(result["pull_request"]["number"], 75)
        self.assertEqual([call[0] for call in connector.calls], ["branch", "update", "pr"])
        self.assertEqual(connector.calls[-1][2], "dev")

    def test_edit_rejects_out_of_contract_path_and_duplicate_ids(self):
        service, _, group = self.service()
        with self.assertRaisesRegex(ValueError, "outside"):
            service.validate_edit("README.md", "changed")
        group["goals"][0]["id"] = "DISCOVERY"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            service.validate_edit("docs/product/map/01-discovery.json", json.dumps(group))


class FeedbackSyncTests(unittest.TestCase):
    def test_pending_feedback_syncs_once_and_discards_message_from_cache(self):
        models = import_api_module("models")
        from packages.connectors.tracker import TrackerIssue
        from services.feedback.sync import sanitize_context, synchronize_pending

        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True))
            session.add(models.IdempotencyRecord(
                project_id="launch-lms", operation="feedback.create", key_hash="a" * 64, status="pending",
                result={"message": "The button is confusing", "context": {"route": "/orgs/acme/private/42"},
                        "environment": "unstable", "opaque_user_id": "user", "opaque_org_id": "org", "role": "learner"},
            )); session.commit()

        class Adapter:
            async def create_issue(self, project, summary, description, properties, idempotency_key):
                self.values = project, summary, description, properties, idempotency_key
                return TrackerIssue("FEED-1", summary, "Open", "r1", properties, {})
            async def set_properties(self, key, properties):
                self.properties = key, properties
            async def get_issue(self, key):
                return TrackerIssue(key, "Feedback", "Open", "r2", {}, {})

        adapter = Adapter()
        result = asyncio.run(synchronize_pending(engine, adapter, load_project("launch-lms").data))
        self.assertEqual(result["synced"], 1)
        self.assertEqual(adapter.values[0], "FEED")
        self.assertEqual(sanitize_context({"route": "/orgs/acme/private/42"})["route"], "/orgs/:id/:id/:id")
        with Session(engine) as session:
            record = session.query(models.IdempotencyRecord).one()
            self.assertEqual(record.status, "synced")
            self.assertEqual(record.result, {"issue_key": "FEED-1", "revision": "r2"})

    def test_tracker_outage_keeps_submission_pending(self):
        models = import_api_module("models")
        from services.feedback.sync import synchronize_pending
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True))
            session.add(models.IdempotencyRecord(
                project_id="launch-lms", operation="feedback.create", key_hash="b" * 64, status="pending",
                result={"message": "Keep me", "context": {}, "environment": "unstable",
                        "opaque_user_id": "user", "opaque_org_id": "org", "role": "learner"},
            )); session.commit()

        class Offline:
            async def create_issue(self, *_args):
                raise httpx.ConnectError("offline")

        result = asyncio.run(synchronize_pending(engine, Offline(), load_project("launch-lms").data))
        self.assertEqual(result["pending"], 1)
        with Session(engine) as session:
            record = session.query(models.IdempotencyRecord).one()
            self.assertEqual(record.status, "pending")
            self.assertEqual(record.result["message"], "Keep me")

    def test_declared_attachment_is_atomic_and_purged_after_tracker_upload(self):
        models = import_api_module("models")
        from packages.connectors.tracker import TrackerIssue
        from services.feedback.sync import PROPERTY, synchronize_pending
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True)); session.flush()
            record = models.IdempotencyRecord(
                project_id="launch-lms", operation="feedback.create", key_hash="c" * 64, status="pending",
                result={"message": "Screenshot attached", "context": {}, "environment": "unstable",
                        "opaque_user_id": "user", "opaque_org_id": "org", "role": "learner", "attachment_count": 1},
            )
            session.add(record); session.commit(); operation_id = record.id

        class Adapter:
            async def create_issue(self, project, summary, description, properties, idempotency_key):
                self.created_property = dict(properties[PROPERTY])
                return TrackerIssue("FEED-2", summary, "Open", "r1", properties, {})
            async def attach(self, key, filename, content_type, stream):
                self.upload = key, filename, content_type, stream.read()
                return {"id": "attachment-1"}
            async def set_properties(self, key, properties):
                self.completed_property = properties[PROPERTY]
            async def get_issue(self, key):
                return TrackerIssue(key, "Screenshot attached", "Open", "r2", {}, {})

        adapter = Adapter()
        waiting = asyncio.run(synchronize_pending(engine, adapter, load_project("launch-lms").data))
        self.assertEqual(waiting["awaiting_attachments"], 1)
        self.assertFalse(hasattr(adapter, "created_property"))
        with Session(engine) as session:
            session.add(models.PendingAttachment(
                operation_id=operation_id, slot=0, opaque_user_id="user", filename="shot.png",
                content_type="image/png", content=b"\x89PNG\r\n\x1a\ncontent", status="pending",
            )); session.commit()
        completed = asyncio.run(synchronize_pending(engine, adapter, load_project("launch-lms").data))
        self.assertEqual(completed["synced"], 1)
        self.assertEqual(adapter.created_property["synchronization_status"], "pending")
        self.assertEqual(adapter.completed_property["synchronization_status"], "complete")
        self.assertEqual(adapter.upload[3], b"\x89PNG\r\n\x1a\ncontent")
        with Session(engine) as session:
            self.assertEqual(session.query(models.PendingAttachment).count(), 0)


class OperationalSchemaTests(unittest.TestCase):
    def test_all_operational_tables_create_without_tracker_content_table(self):
        models = import_api_module("models")
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        self.assertEqual(
            set(inspect(engine).get_table_names()),
            {"projects", "embed_sessions", "unread_markers", "sync_cursors", "idempotency_records", "pending_attachments", "agent_runs", "deployment_observations", "announcements"},
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


class EmbedSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        cls.jwt = jwt
        cls.private = Ed25519PrivateKey.generate()
        cls.next_private = Ed25519PrivateKey.generate()
        cls.public = cls.private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.next_public = cls.next_private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        import_api_module("config")
        import_api_module("auth")
        import_api_module("models")
        cls.embed = import_api_module("embed_auth")

    def settings(self):
        config = sys.modules["config"]
        return config.Settings(
            github_client_id="client", github_client_secret="secret", session_secret="x" * 32,
            embed_public_keys_json=json.dumps({"launch-ops-2026-01": self.public, "launch-ops-2026-02": self.next_public}),
        )

    def claims(self, **changes):
        now = int(datetime.now(timezone.utc).timestamp())
        value = {"project": "launch-lms", "environment": "unstable", "sub": "opaque-user", "org": "opaque-org",
                 "role": "learner", "nonce": str(uuid4()), "iat": now, "exp": now + 240,
                 "aud": "launch-operations", "iss": "launch-lms"}
        value.update(changes)
        return value

    def token(self, claims, next_key=False):
        return self.jwt.encode(claims, self.next_private if next_key else self.private, algorithm="EdDSA",
                               headers={"kid": "launch-ops-2026-02" if next_key else "launch-ops-2026-01"})

    def verify(self, claims, origin="https://tenant.life2launch.dev", store=None, next_key=False):
        return self.embed.verify_host_token(
            self.token(claims, next_key), claims["nonce"], origin, load_project("launch-lms"),
            self.settings(), store or self.embed.MemoryReplayStore(),
        )

    def test_valid_and_next_rotation_keys(self):
        self.assertEqual(self.verify(self.claims())["sub"], "opaque-user")
        self.assertEqual(self.verify(self.claims(), next_key=True)["sub"], "opaque-user")

    def test_expired_future_audience_origin_and_replay_are_rejected(self):
        from fastapi import HTTPException
        now = int(datetime.now(timezone.utc).timestamp())
        invalid = [
            self.claims(iat=now - 400, exp=now - 100),
            self.claims(iat=now + 60, exp=now + 120),
            self.claims(aud="wrong-audience"),
        ]
        for claims in invalid:
            with self.assertRaises(HTTPException):
                self.verify(claims)
        with self.assertRaisesRegex(HTTPException, "Host origin"):
            self.verify(self.claims(), origin="https://attacker.example")
        claims = self.claims(); store = self.embed.MemoryReplayStore()
        self.verify(claims, store=store)
        with self.assertRaisesRegex(HTTPException, "already used"):
            self.verify(claims, store=store)

    def test_loader_never_places_session_token_in_url_or_storage(self):
        loader = (ROOT / "packages/embed-sdk/loader.js").read_text()
        self.assertIn("new MessageChannel()", loader)
        self.assertNotIn("localStorage", loader)
        self.assertNotIn("sessionStorage", loader)
        self.assertNotIn("token=", loader)

    def test_embed_document_is_origin_scoped_and_production_disabled(self):
        from fastapi import HTTPException
        main = import_api_module("main")
        response = main.embed_document("launch-lms", "unstable")
        policy = response.headers["content-security-policy"]
        self.assertIn("frame-ancestors https://life2launch.dev https://*.life2launch.dev", policy)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with self.assertRaises(HTTPException) as error:
            main.embed_document("launch-lms", "production")
        self.assertEqual(error.exception.status_code, 404)

    def test_feedback_acceptance_is_durable_and_idempotent(self):
        main = import_api_module("main")
        models = sys.modules["models"]
        engine = create_engine("sqlite:///:memory:")
        models.Base.metadata.create_all(engine)
        from sqlalchemy.orm import Session
        with Session(engine) as session:
            session.add(models.Project(id="launch-lms", manifest_revision="test", enabled=True)); session.commit()
        main.app.state.engine = engine
        identity = {"project": "launch-lms", "environment": "unstable", "sub": "user", "org": "org", "role": "learner"}
        payload = main.FeedbackSubmission(message="The button is confusing", context={"route": "/test", "private": "drop"})
        first = main.submit_embed_feedback(payload, identity, "same-key")
        second = main.submit_embed_feedback(payload, identity, "same-key")
        self.assertEqual(first, second)
        with Session(engine) as session:
            record = session.query(models.IdempotencyRecord).one()
            self.assertEqual(record.status, "pending")
            self.assertNotIn("private", record.result["context"])


if __name__ == "__main__":
    unittest.main()
