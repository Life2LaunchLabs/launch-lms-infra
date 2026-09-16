"""Validate immutable application candidate and environment-lock contracts."""

from __future__ import annotations

import re


SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_candidate(candidate: dict, manifest: dict) -> dict:
    required = set(manifest["candidate"]["required_fields"])
    missing = required - set(candidate)
    if candidate.get("schema_version") != manifest["candidate"]["schema_version"] or missing:
        raise ValueError(f"invalid candidate schema; missing {sorted(missing)}")
    if not SHA.fullmatch(candidate["source_sha"]):
        raise ValueError("candidate source_sha must be exact")
    if not DIGEST.fullmatch(candidate["image_digest"]):
        raise ValueError("candidate image_digest must be immutable")
    if set(candidate["architectures"]) != {"amd64", "arm64"}:
        raise ValueError("candidate must be verified for amd64 and arm64")
    if not candidate.get("build_run_id"):
        raise ValueError("candidate requires verification run")
    return candidate


def environment_lock(candidate: dict, environment: str) -> dict:
    return {
        "schema_version": 1,
        "environment": environment,
        "source_sha": candidate["source_sha"],
        "image": candidate["image"],
        "image_digest": candidate["image_digest"],
        "version": candidate["version"],
        "migration": candidate["migration"],
        "verification_run": candidate["build_run_id"],
    }
