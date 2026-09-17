"""Emit a small, host-observed release record after an unstable deploy succeeds."""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/launch-lms")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, cwd=ROOT).strip()


def observe(run_id: str, run_attempt: str) -> dict:
    if not run_id.isdecimal() or not run_attempt.isdecimal():
        raise ValueError("A numeric deployment workflow run and attempt are required")
    if (ROOT / ".deployment-environment").read_text().strip() != "unstable":
        raise ValueError("The observed host is not unstable")
    lock = json.loads((ROOT / ".deploy-state/deployed-release.json").read_text())
    source_sha, digest = lock.get("commit_sha"), lock.get("image_digest")
    if not isinstance(source_sha, str) or not SHA.fullmatch(source_sha):
        raise ValueError("The deployed release lacks an exact source SHA")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError("The deployed release lacks an exact image digest")
    if lock.get("image_ref") != f"ghcr.io/life2launchlabs/launch-lms@{digest}":
        raise ValueError("The deployed image reference differs from its digest")
    if str(lock.get("build_run_id", "")).isdecimal() is False:
        raise ValueError("The deployed release lacks a build run")

    containers = command(
        "docker", "ps", "--filter", "label=com.docker.compose.project=launch-lms",
        "--filter", "label=com.docker.compose.service=launch-lms", "--format", "{{.ID}}",
    ).splitlines()
    if len(containers) != 1:
        raise ValueError("Expected exactly one running Launch LMS container")
    container_id = containers[0]
    actual_image_id = command("docker", "inspect", container_id, "--format", "{{.Image}}")
    locked_image_id = command("docker", "image", "inspect", lock["image_ref"], "--format", "{{.Id}}")
    if actual_image_id != locked_image_id:
        raise ValueError("The running container does not use the deployed image")
    build = json.loads(command("docker", "exec", container_id, "cat", "/app/build-info.json"))
    if build.get("commit_sha") != source_sha or build.get("source_branch") != "dev":
        raise ValueError("The running application does not match the release source")
    infra_sha = command("git", "rev-parse", "HEAD")
    if not SHA.fullmatch(infra_sha):
        raise ValueError("The infrastructure checkout has no exact SHA")
    return {
        "schema_version": 1, "environment": "unstable", "source_sha": source_sha,
        "image_digest": digest, "build_run_id": str(lock["build_run_id"]),
        "infra_sha": infra_sha, "deploy_run_id": run_id, "deploy_run_attempt": run_attempt,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    print(json.dumps(observe(sys.argv[1], sys.argv[2]), separators=(",", ":")))
