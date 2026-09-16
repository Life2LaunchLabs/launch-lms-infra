"""Read and propose changes to canonical repository product documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
from uuid import uuid4

from packages.connectors.github import GitHubConnector
from services.orchestrator.manifest import ProjectManifest, require_sha


GROUP_ID = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
EDITABLE = re.compile(r"^(docs/product/map/[0-9]{2}-[a-z0-9_]+\.json|docs/design/[A-Za-z0-9_./-]+\.md)$")


@dataclass(frozen=True)
class RepositoryPlanning:
    connector: GitHubConnector
    manifest: ProjectManifest

    @property
    def repository(self) -> str:
        return self.manifest.repository

    @property
    def branch(self) -> str:
        return self.manifest.data["source"]["delivery_branch"]

    async def revision(self) -> str:
        return require_sha(await self.connector.branch_head(self.repository, self.branch))

    async def summary(self) -> dict:
        revision = await self.revision()
        product_path = self.manifest.data["source"]["policy_paths"]["product"]
        manifest_text, manifest_blob = await self.connector.text_file(self.repository, product_path, revision)
        migration = json.loads(manifest_text)
        return {
            "project_id": self.manifest.project_id, "repository": self.repository, "branch": self.branch,
            "source_revision": revision, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "manifest_path": product_path, "manifest_blob_sha": manifest_blob, "migration": migration,
        }

    async def catalog(self) -> dict:
        result = await self.summary()
        revision = result["source_revision"]
        product_path = result["manifest_path"]
        directory = str(PurePosixPath(product_path).parent / "map")
        entries = await self.connector.contents(self.repository, directory, revision)
        if not isinstance(entries, list):
            raise ValueError("product map path must be a directory")
        groups = []
        for entry in sorted(entries, key=lambda value: value["name"]):
            if entry.get("type") != "file" or not entry["name"].endswith(".json"):
                continue
            text, blob_sha = await self.connector.text_file(self.repository, entry["path"], revision)
            group = json.loads(text)
            goals = group.get("goals", [])
            groups.append({
                "id": group["id"], "title": group["title"], "intent": group.get("intent"),
                "path": entry["path"], "blob_sha": blob_sha, "goal_count": len(goals),
                "activity_count": sum(len(goal.get("activities", [])) for goal in goals),
                "step_count": sum(len(activity.get("steps", [])) for goal in goals for activity in goal.get("activities", [])),
                "goals": [{
                    "id": goal["id"], "title": goal["title"], "outcome": goal.get("outcome"),
                    "activity_count": len(goal.get("activities", [])),
                } for goal in goals],
            })
        return {**result, "groups": groups}

    async def group(self, group_id: str) -> dict:
        if not GROUP_ID.fullmatch(group_id):
            raise ValueError("invalid product group id")
        summary = await self.summary()
        directory = str(PurePosixPath(summary["manifest_path"]).parent / "map")
        entries = await self.connector.contents(self.repository, directory, summary["source_revision"])
        entry = next((value for value in entries if value.get("type") == "file" and
                      value["name"].split("-", 1)[-1].removesuffix(".json").upper() == group_id), None)
        if not entry:
            raise KeyError(group_id)
        text, blob_sha = await self.connector.text_file(self.repository, entry["path"], summary["source_revision"])
        return {
            "source_revision": summary["source_revision"], "fetched_at": summary["fetched_at"],
            "path": entry["path"], "blob_sha": blob_sha, "document": json.loads(text),
        }

    async def design_index(self) -> dict:
        revision = await self.revision()
        path = self.manifest.data["source"]["policy_paths"]["design"]
        content, blob_sha = await self.connector.text_file(self.repository, path, revision)
        return {"source_revision": revision, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "path": path, "blob_sha": blob_sha, "content": content}

    @staticmethod
    def validate_edit(path: str, content: str) -> None:
        if not EDITABLE.fullmatch(path) or ".." in PurePosixPath(path).parts:
            raise ValueError("path is outside the repository planning contract")
        if len(content.encode()) > 2 * 1024 * 1024:
            raise ValueError("planning document exceeds 2 MiB")
        if path.endswith(".json"):
            document = json.loads(content)
            if not isinstance(document, dict) or not GROUP_ID.fullmatch(document.get("id", "")):
                raise ValueError("product group document is invalid")
            seen = set()
            for item in [document, *document.get("goals", [])]:
                identifier = item.get("id", "")
                if not identifier or identifier in seen:
                    raise ValueError("product document contains a missing or duplicate id")
                seen.add(identifier)
            for goal in document.get("goals", []):
                for activity in goal.get("activities", []):
                    for item in [activity, *activity.get("steps", [])]:
                        identifier = item.get("id", "")
                        if not identifier or identifier in seen:
                            raise ValueError("product document contains a missing or duplicate id")
                        seen.add(identifier)

    async def propose_edit(
        self, *, path: str, content: str, expected_blob_sha: str, base_sha: str, title: str, reason: str,
    ) -> dict:
        self.validate_edit(path, content)
        require_sha(expected_blob_sha)
        require_sha(base_sha)
        current_head = await self.revision()
        if current_head != base_sha:
            raise RuntimeError("repository advanced; refresh before proposing an edit")
        _, current_blob = await self.connector.text_file(self.repository, path, base_sha)
        if current_blob != expected_blob_sha:
            raise RuntimeError("planning document advanced; refresh before proposing an edit")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:36] or "product-edit"
        branch = f"planning/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{slug}-{uuid4().hex[:6]}"
        await self.connector.create_branch(self.repository, branch, base_sha)
        commit = await self.connector.update_file(
            self.repository, path, branch, content, current_blob, f"docs: {title.strip()[:64]}",
        )
        pull = await self.connector.create_pull_request(
            self.repository, title.strip()[:120],
            f"## Planning change\n\n{reason.strip()}\n\nSource revision: `{base_sha}`\nCanonical file: `{path}`\n\nCreated by the authenticated operations control plane. CI and owner review remain required.",
            branch, self.branch,
        )
        return {"branch": branch, "commit_sha": commit["commit"]["sha"],
                "pull_request": {"number": pull["number"], "url": pull["html_url"]}}
