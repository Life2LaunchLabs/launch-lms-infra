"""Read-only portal and Symphony projection contracts."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/control-plane/api"))
from config import Settings
from deployment_evidence import reconcile
from main import app
from orchestration import sanitized_status


class StatusTests(unittest.TestCase):
    def test_only_allowlisted_symphony_fields_survive(self):
        current = datetime.now(timezone.utc).isoformat()
        value = sanitized_status({
            "generated_at": current,
            "running": [{"issue_identifier": "BOT-205", "started_at": current,
                         "turn_count": 3, "last_message": "private /home/node/workspaces/secret",
                         "workspace": "/home/node/workspaces/secret", "tokens": {"total_tokens": 99}}],
            "retrying": [{"issue_identifier": "BOT-253", "attempt": 2,
                          "error": "credential leaked at /run/secrets/key"}],
            "blocked": [{"issue_identifier": "BOT-205", "blocked_at": current,
                         "error": "private /home/node/workspaces/secret"}],
            "rate_limits": {"private": "value"},
        })
        self.assertEqual(value["availability"], "live")
        self.assertEqual(value["running"][0]["issue_url"], "https://henrydker.atlassian.net/browse/BOT-205")
        self.assertEqual(value["retrying"][0]["reason"], "Retry scheduled")
        self.assertEqual(value["blocked"][0]["reason"], "Agent blocked; inspect the delivery issue")
        self.assertNotIn("private", str(value))
        self.assertNotIn("workspace", str(value))

    def test_missing_generation_is_stale(self):
        self.assertEqual(sanitized_status({"running": [], "retrying": [], "blocked": []})["availability"], "stale")
        with self.assertRaises(ValueError):
            sanitized_status(["unexpected"])
        with self.assertRaises(ValueError):
            sanitized_status({"generated_at": datetime.now(timezone.utc).isoformat()})
        with self.assertRaises(ValueError):
            sanitized_status({"generated_at": datetime.now(timezone.utc).isoformat(),
                              "error": {"code": "snapshot_timeout"}})


class ReadOnlyTests(unittest.TestCase):
    def test_all_api_writes_are_blocked_except_logout(self):
        app.state.settings = Settings(read_only=True)

        async def exercise():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for path in (
                    "/api/v1/projects/launch-lms/deployments/unstable/dispatch",
                    "/api/v1/projects/launch-lms/deployments/unstable/observe",
                    "/api/v1/projects/launch-lms/environments/unstable/announcements",
                    "/api/v1/embed/session", "/api/v1/embed/feedback",
                    "/api/v1/embed/feedback/FEED-1/reply",
                ):
                    for method in ("POST", "PUT", "PATCH", "DELETE"):
                        response = await asyncio.wait_for(client.request(method, path), timeout=2)
                        self.assertEqual(response.status_code, 403, (method, path))
                self.assertEqual((await asyncio.wait_for(client.post("/api/v1/auth/logout"), timeout=2)).status_code, 204)

        asyncio.run(exercise())


class DeploymentEvidenceTests(unittest.TestCase):
    def test_exact_candidate_workflow_and_host_agreement_is_required(self):
        source, digest = "a" * 40, "sha256:" + "b" * 64
        observation = {"schema_version": 1, "environment": "unstable", "source_sha": source,
                       "image_digest": digest, "build_run_id": "12", "infra_sha": "c" * 40,
                       "deploy_run_id": "34", "deploy_run_attempt": "1",
                       "observed_at": "2026-09-17T22:00:00+00:00"}
        candidate = {"build_run_id": "12", "commit_sha": source, "image_digest": digest,
                     "image_ref": "ghcr.io/life2launchlabs/launch-lms@" + digest}
        deploy = {"id": 34, "run_attempt": 1, "head_sha": "c" * 40}
        build = {"id": 12, "head_sha": source}
        self.assertEqual(reconcile(observation, candidate, deploy, build)["state"], "deployed")
        self.assertEqual(reconcile({**observation, "image_digest": "sha256:" + "d" * 64}, candidate, deploy, build)["state"], "mismatch")
        self.assertEqual(reconcile(observation, {**candidate, "commit_sha": "e" * 40}, deploy, build)["state"], "mismatch")
        self.assertEqual(reconcile(observation, candidate, {**deploy, "run_attempt": 2}, build)["state"], "mismatch")


if __name__ == "__main__":
    unittest.main()
