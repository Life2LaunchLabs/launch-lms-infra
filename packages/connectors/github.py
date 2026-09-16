"""GitHub capability boundary for workflow observation and dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
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
        return GitHubConnector(value["token"], api_url=api_url, transport=transport)


@dataclass(frozen=True)
class GitHubConnector:
    token: str = field(repr=False)
    api_url: str = "https://api.github.com"
    transport: httpx.AsyncBaseTransport | None = None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.api_url, timeout=30, transport=self.transport, headers=self.headers())

    async def branch_head(self, repository: str, branch: str) -> str:
        async with self._client() as client:
            response = await client.get(f"/repos/{repository}/git/ref/heads/{branch}")
            response.raise_for_status()
            return response.json()["object"]["sha"]

    async def contents(self, repository: str, path: str, ref: str) -> dict | list[dict]:
        async with self._client() as client:
            response = await client.get(f"/repos/{repository}/contents/{path}", params={"ref": ref})
            response.raise_for_status()
            return response.json()

    async def text_file(self, repository: str, path: str, ref: str) -> tuple[str, str]:
        value = await self.contents(repository, path, ref)
        if not isinstance(value, dict) or value.get("type") != "file" or value.get("encoding") != "base64":
            raise ValueError(f"GitHub path is not a base64 file: {path}")
        encoded = "".join(value["content"].split())
        return base64.b64decode(encoded, validate=True).decode("utf-8"), value["sha"]

    async def create_branch(self, repository: str, branch: str, source_sha: str) -> None:
        async with self._client() as client:
            response = await client.post(f"/repos/{repository}/git/refs", json={
                "ref": f"refs/heads/{branch}", "sha": source_sha,
            })
            response.raise_for_status()

    async def update_file(
        self, repository: str, path: str, branch: str, content: str, blob_sha: str, message: str,
    ) -> dict:
        async with self._client() as client:
            response = await client.put(f"/repos/{repository}/contents/{path}", json={
                "message": message, "content": base64.b64encode(content.encode()).decode(),
                "sha": blob_sha, "branch": branch,
            })
            response.raise_for_status()
            return response.json()

    async def create_pull_request(
        self, repository: str, title: str, body: str, head: str, base: str,
    ) -> dict:
        async with self._client() as client:
            response = await client.post(f"/repos/{repository}/pulls", json={
                "title": title, "body": body, "head": head, "base": base,
            })
            response.raise_for_status()
            return response.json()

    async def workflow_runs(self, repository: str, branch: str, limit: int = 20) -> list[dict]:
        async with self._client() as client:
            response = await client.get(f"/repos/{repository}/actions/runs", params={
                "branch": branch, "per_page": min(limit, 100),
            })
            response.raise_for_status()
            return response.json().get("workflow_runs", [])

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
