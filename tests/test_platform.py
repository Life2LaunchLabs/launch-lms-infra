"""Contracts for the reusable operations-platform foundation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from sqlalchemy import create_engine, inspect


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


if __name__ == "__main__":
    unittest.main()
