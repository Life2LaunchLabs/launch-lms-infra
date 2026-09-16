"""GitHub capability boundary for workflow observation and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
import httpx


@dataclass(frozen=True)
class GitHubConnector:
    token: str
    api_url: str = "https://api.github.com"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}

    async def dispatch(self, repository: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.api_url}/repos/{repository}/actions/workflows/{workflow}/dispatches",
                headers=self.headers(), json={"ref": ref, "inputs": inputs},
            )
            response.raise_for_status()

    async def workflow_run(self, repository: str, run_id: int) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.api_url}/repos/{repository}/actions/runs/{run_id}", headers=self.headers()
            )
            response.raise_for_status()
            return response.json()
