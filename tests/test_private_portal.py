"""Read-only portal and Symphony projection contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/control-plane/api"))
from config import Settings
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
        self.assertEqual(value["running"][0]["issue_url"], "https://life2launch.atlassian.net/browse/BOT-205")
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


if __name__ == "__main__":
    unittest.main()
