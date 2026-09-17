"""Jira adapter used by platform services; credentials never reach worker shells."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import BinaryIO
from urllib.parse import urlparse

import httpx

from packages.connectors.tracker import TrackerAdapter, TrackerIssue


@dataclass(frozen=True)
class JiraCredentials:
    base_url: str
    email: str
    api_token: str = field(repr=False)


class JiraAdapter(TrackerAdapter):
    def __init__(self, credentials: JiraCredentials, transport: httpx.AsyncBaseTransport | None = None):
        self.credentials = credentials
        self.transport = transport
        base = urlparse(credentials.base_url.rstrip("/"))
        if base.scheme != "https" or not base.hostname or base.username or base.password or base.query or base.fragment:
            raise ValueError("Jira base URL must be HTTPS without query or fragment")
        prefix = base.path.rstrip("/")
        if base.hostname == "api.atlassian.com":
            if not prefix.startswith("/ex/jira/") or len(prefix.split("/")) != 4:
                raise ValueError("Jira gateway URL must be https://api.atlassian.com/ex/jira/<cloudId>")
        elif prefix:
            raise ValueError("Jira gateway URL must be https://api.atlassian.com/ex/jira/<cloudId>")
        self._origin = f"{base.scheme}://{base.netloc}"
        self._api_prefix = prefix

    def _path(self, route: str) -> str:
        return self._api_prefix + route

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._origin,
            auth=(self.credentials.email, self.credentials.api_token),
            headers={"Accept": "application/json"}, timeout=30, transport=self.transport,
        )

    @staticmethod
    def _issue(value: dict) -> TrackerIssue:
        fields = value.get("fields", {})
        return TrackerIssue(
            key=value["key"], summary=fields.get("summary", ""),
            status=fields.get("status", {}).get("name", ""),
            revision=fields.get("updated", ""), properties=value.get("properties", {}), fields=fields,
        )

    async def create_issue(self, project: str, summary: str, description: dict, properties: dict, idempotency_key: str) -> TrackerIssue:
        async with self._client() as client:
            response = await client.post(self._path("/rest/api/3/issue"), json={
                "fields": {
                    "project": {"key": project}, "issuetype": {"name": "Task"},
                    "summary": summary, "description": description,
                },
                "properties": [{"key": name, "value": value} for name, value in properties.items()],
            }, headers={"X-Idempotency-Key": idempotency_key})
            response.raise_for_status()
            key = response.json()["key"]
        return await self.get_issue(key)

    async def get_issue(self, key: str) -> TrackerIssue:
        async with self._client() as client:
            response = await client.get(self._path(f"/rest/api/3/issue/{key}"), params={
                "fields": "summary,status,updated", "properties": "*all",
            })
            response.raise_for_status()
            return self._issue(response.json())

    async def list_issues(self, project: str) -> list[TrackerIssue]:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", project):
            raise ValueError("tracker project key is invalid")
        issues = []
        token = None
        seen_tokens = set()
        async with self._client() as client:
            while len(issues) < 1000:
                params = {
                    "jql": f'project = "{project}" ORDER BY updated DESC',
                    "fields": "summary,status,updated,priority,labels,attachment,description",
                    "properties": "*all", "maxResults": 100,
                }
                if token:
                    params["nextPageToken"] = token
                response = await client.get(self._path("/rest/api/3/search/jql"), params=params)
                response.raise_for_status()
                value = response.json()
                issues.extend(self._issue(item) for item in value.get("issues", []))
                next_token = value.get("nextPageToken")
                if value.get("isLast") is True or not next_token or next_token in seen_tokens:
                    break
                seen_tokens.add(next_token)
                token = next_token
        return issues[:1000]

    async def comments(self, key: str) -> list[dict]:
        comments = []
        start_at = 0
        async with self._client() as client:
            while len(comments) < 1000:
                response = await client.get(self._path(f"/rest/api/3/issue/{key}/comment"), params={
                    "orderBy": "created", "startAt": start_at, "maxResults": 100,
                })
                response.raise_for_status()
                value = response.json()
                page = value.get("comments", [])
                comments.extend(page)
                start_at += len(page)
                if not page or start_at >= int(value.get("total", start_at)):
                    break
        return comments[:1000]

    async def add_comment(self, key: str, body: dict, public: bool) -> dict:
        async with self._client() as client:
            if public:
                response = await client.post(self._path(f"/rest/servicedeskapi/request/{key}/comment"), json={"body": body, "public": True})
            else:
                response = await client.post(self._path(f"/rest/api/3/issue/{key}/comment"), json={"body": body})
            response.raise_for_status()
            return response.json()

    async def attach(self, key: str, filename: str, content_type: str, stream: BinaryIO) -> dict:
        async with self._client() as client:
            response = await client.post(
                self._path(f"/rest/api/3/issue/{key}/attachments"),
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, stream, content_type)},
            )
            response.raise_for_status()
            values = response.json()
            return values[0] if values else {}

    async def transition(self, key: str, status: str) -> None:
        async with self._client() as client:
            response = await client.get(self._path(f"/rest/api/3/issue/{key}/transitions"))
            response.raise_for_status()
            transition = next((item for item in response.json().get("transitions", []) if item.get("to", {}).get("name") == status), None)
            if not transition:
                raise ValueError(f"transition to {status!r} is unavailable for {key}")
            result = await client.post(self._path(f"/rest/api/3/issue/{key}/transitions"), json={"transition": {"id": transition["id"]}})
            result.raise_for_status()

    async def set_properties(self, key: str, properties: dict) -> None:
        async with self._client() as client:
            for name, value in properties.items():
                response = await client.put(self._path(f"/rest/api/3/issue/{key}/properties/{name}"), json=value)
                response.raise_for_status()
