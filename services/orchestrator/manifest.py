"""Load and strictly validate reusable project registrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ProjectManifest:
    path: Path
    data: dict[str, Any]

    @property
    def project_id(self) -> str:
        return self.data["project_id"]

    @property
    def repository(self) -> str:
        return self.data["source"]["repository"]

    @property
    def workflow_path(self) -> str:
        return self.data["source"]["policy_paths"]["workflow"]


def load_project(project_id: str, root: Path = ROOT) -> ProjectManifest:
    if not PROJECT_ID.fullmatch(project_id):
        raise ValueError("invalid project id")
    path = root / "projects" / project_id / "project.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate(data)
    token = data["token_verification"]
    for key_path in token["public_keys"].values():
        resolved = (path.parent / key_path).resolve()
        if path.parent.resolve() not in resolved.parents or not resolved.is_file():
            raise ValueError("token public key must be a project-local file")
    return ProjectManifest(path=path, data=data)


def validate(data: Any) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("project manifest schema_version must be 1")
    project_id = data.get("project_id", "")
    if not PROJECT_ID.fullmatch(project_id):
        raise ValueError("project_id must be a lowercase slug")
    repository = data.get("source", {}).get("repository", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("source.repository must be owner/name")
    paths = data["source"].get("policy_paths", {})
    for name in ("agents", "workflow", "architecture", "product", "feedback"):
        value = paths.get(name)
        if not value or value.startswith(("/", "../")):
            raise ValueError(f"invalid policy path: {name}")
    statuses = data.get("tracker", {}).get("statuses", {})
    if set(statuses) != {"ready", "active", "review", "approved", "complete"}:
        raise ValueError("tracker statuses must define the complete lifecycle")
    checks = data.get("required_checks")
    if not isinstance(checks, list) or not checks or len(checks) != len(set(checks)):
        raise ValueError("required_checks must be a non-empty unique list")
    deployment = data.get("deployment", {})
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", deployment.get("repository", "")):
        raise ValueError("deployment.repository must be owner/name")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", deployment.get("workflow", "")):
        raise ValueError("deployment.workflow must be a workflow filename")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", deployment.get("ref", "")):
        raise ValueError("deployment.ref is invalid")
    environments = data.get("environments", {})
    origins = data.get("allowed_embed_origins", {})
    if set(environments) != set(origins):
        raise ValueError("every environment must define allowed embed origins")
    if environments.get("production", {}).get("embed_enabled") and not origins.get("production"):
        raise ValueError("enabled production embed requires explicit origins")
    token = data.get("token_verification", {})
    if token.get("algorithm") != "EdDSA" or token.get("current_key_id") == token.get("next_key_id"):
        raise ValueError("EdDSA current and next key ids are required")
    key_ids = {token.get("current_key_id"), token.get("next_key_id")}
    public_keys = token.get("public_keys", {})
    if set(public_keys) != key_ids:
        raise ValueError("token public keys must contain exactly current and next key ids")
    if any(not isinstance(path, str) or path.startswith(("/", "../")) for path in public_keys.values()):
        raise ValueError("token public key paths must be project-relative")
    required_modules = {"orchestration", "feedback", "announcements", "release_notes", "deployment_observer", "planning"}
    if set(data.get("modules", {})) != required_modules:
        raise ValueError("module flags are incomplete")


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise ValueError("exact 40-character commit SHA required")
    return value
