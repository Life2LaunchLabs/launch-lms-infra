"""GitHub capability boundary for workflow observation and dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import httpx
import jwt


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: str
    installation_id: str
    private_key: str = field(repr=False)

    async def connector(self, api_url: str = "https://api.github.com", transport: httpx.AsyncBaseTransport | None = None) -> "GitHubConnector":
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
            self.private_key.replace("\\n", "\n"), algorithm="RS256",
        )
        async with httpx.AsyncClient(base_url=api_url, timeout=30, transport=transport) as client:
            response = await client.post(
                f"/app/installations/{self.installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {assertion}", "Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            value = response.json()
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise RuntimeError("GitHub returned an expired installation token")
        return GitHubConnector(value["token"], api_url=api_url, transport=transport, expires_at=expires)


@dataclass(frozen=True)
class GitHubConnector:
    token: str = field(repr=False)
    api_url: str = "https://api.github.com"
    transport: httpx.AsyncBaseTransport | None = None
    expires_at: datetime | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    async def dispatch(self, repository: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.post(
                f"{self.api_url}/repos/{repository}/actions/workflows/{workflow}/dispatches",
                headers=self.headers(), json={"ref": ref, "inputs": inputs},
            )
            response.raise_for_status()

    async def repository_dispatch(self, repository: str, event_type: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.post(
                f"{self.api_url}/repos/{repository}/dispatches",
                headers=self.headers(), json={"event_type": event_type, "client_payload": payload},
            )
            response.raise_for_status()

    async def workflow_run(self, repository: str, run_id: int) -> dict:
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.get(
                f"{self.api_url}/repos/{repository}/actions/runs/{run_id}", headers=self.headers()
            )
            response.raise_for_status()
            return response.json()
