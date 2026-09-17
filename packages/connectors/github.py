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

    async def connector(self, repository: str, permissions: dict[str, str],
                        api_url: str = "https://api.github.com",
                        transport: httpx.AsyncBaseTransport | None = None) -> "GitHubConnector":
        if repository.count("/") != 1 or not all(repository.split("/")):
            raise ValueError("A single owner/repository is required for installation tokens")
        if not permissions or any(level not in {"read", "write"} for level in permissions.values()):
            raise ValueError("Explicit installation-token permissions are required")
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.app_id},
            self.private_key.replace("\\n", "\n"), algorithm="RS256",
        )
        async with httpx.AsyncClient(base_url=api_url, timeout=30, transport=transport) as client:
            response = await client.post(
                f"/app/installations/{self.installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {assertion}", "Accept": "application/vnd.github+json"},
                json={"repositories": [repository.split("/", 1)[1]], "permissions": permissions},
            )
            response.raise_for_status()
            value = response.json()
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise RuntimeError("GitHub returned an expired installation token")
        return GitHubConnector(value["token"], repository, api_url=api_url, transport=transport)


@dataclass(frozen=True)
class GitHubConnector:
    token: str = field(repr=False)
    repository: str
    api_url: str = "https://api.github.com"
    transport: httpx.AsyncBaseTransport | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    async def dispatch(self, repository: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self._require_repository(repository)
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.post(
                f"{self.api_url}/repos/{repository}/actions/workflows/{workflow}/dispatches",
                headers=self.headers(), json={"ref": ref, "inputs": inputs},
            )
            response.raise_for_status()

    async def repository_dispatch(self, repository: str, event_type: str, payload: dict) -> None:
        self._require_repository(repository)
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.post(
                f"{self.api_url}/repos/{repository}/dispatches",
                headers=self.headers(), json={"event_type": event_type, "client_payload": payload},
            )
            response.raise_for_status()

    async def workflow_run(self, repository: str, run_id: int) -> dict:
        self._require_repository(repository)
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.get(
                f"{self.api_url}/repos/{repository}/actions/runs/{run_id}", headers=self.headers()
            )
            response.raise_for_status()
            return response.json()

    async def collaborator_has_read_access(self, repository: str, username: str) -> bool:
        """Check current repository access using an installation-scoped token."""
        self._require_repository(repository)
        if not username or "/" in username:
            raise ValueError("A GitHub username is required")
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.get(
                f"{self.api_url}/repos/{repository}/collaborators/{username}/permission",
                headers=self.headers(),
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return response.json().get("permission") in {"read", "write", "admin"}

    def _require_repository(self, repository: str) -> None:
        if repository != self.repository:
            raise ValueError("Installation token is scoped to a different repository")
