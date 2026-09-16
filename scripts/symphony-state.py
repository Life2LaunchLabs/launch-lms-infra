#!/usr/bin/env python3
"""Inspect, snapshot, and verify Symphony state without exposing credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.symphony.yml"
SAFE_VOLUME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")
MINIMUM_WORKER_MEMORY = 4 * 1024**3
MINIMUM_HOST_MEMORY = 8 * 1024**3


def command(*parts: str) -> str:
    return subprocess.check_output(parts, text=True).strip()


def container_id() -> str:
    return command("docker", "compose", "-f", str(COMPOSE), "ps", "-q", "--all", "symphony")


def meminfo() -> dict[str, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.strip().split()[0]) * 1024
    return values


def cgroup_metrics(identifier: str) -> dict:
    def number(name: str) -> int | None:
        try:
            return int(command("docker", "exec", identifier, "cat", f"/sys/fs/cgroup/{name}"))
        except (subprocess.CalledProcessError, ValueError):
            return None

    events = {}
    try:
        raw = command("docker", "exec", identifier, "cat", "/sys/fs/cgroup/memory.events")
        events = {name: int(value) for name, value in (line.split() for line in raw.splitlines())}
    except (subprocess.CalledProcessError, ValueError):
        pass
    return {"current_bytes": number("memory.current"), "peak_bytes": number("memory.peak"), "events": events}


def inspect() -> dict:
    identifier = container_id()
    if not identifier:
        raise RuntimeError("Symphony container does not exist")
    raw = json.loads(command("docker", "inspect", identifier))[0]
    host = raw["HostConfig"]
    state = raw["State"]
    mounts = {mount["Destination"]: mount["Name"] for mount in raw["Mounts"] if mount["Type"] == "volume"}
    host_memory = meminfo()
    return {
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "container_id": identifier,
        "image": raw["Config"]["Image"],
        "image_id": raw["Image"],
        "running": state["Running"],
        "health": state.get("Health", {}).get("Status"),
        "exit_code": state["ExitCode"],
        "oom_killed": state["OOMKilled"],
        "restart_count": raw["RestartCount"],
        "memory_limit_bytes": host["Memory"],
        "nano_cpus": host["NanoCpus"],
        "pids_limit": host.get("PidsLimit"),
        "home_volume": mounts.get("/home/node"),
        "memory": cgroup_metrics(identifier) if state["Running"] else None,
        "host_memory": {
            "total_bytes": host_memory.get("MemTotal", 0),
            "available_bytes": host_memory.get("MemAvailable", 0),
            "swap_total_bytes": host_memory.get("SwapTotal", 0),
        },
    }


def preflight(state: dict) -> dict:
    blockers = []
    warnings = []
    if not state.get("running"):
        blockers.append("worker is not running")
    if state.get("health") not in (None, "healthy"):
        blockers.append(f"worker health is {state['health']}")
    if not state.get("home_volume"):
        blockers.append("persistent home volume is missing")
    if state.get("oom_killed"):
        blockers.append("container records an OOM kill")
    events = (state.get("memory") or {}).get("events", {})
    if events.get("oom", 0) or events.get("oom_kill", 0):
        blockers.append(f"cgroup records {events.get('oom', 0)} OOM events and {events.get('oom_kill', 0)} kills")
    limit = state.get("memory_limit_bytes") or 0
    if limit < MINIMUM_WORKER_MEMORY:
        blockers.append("worker memory limit is below 4 GiB")
    host_total = state.get("host_memory", {}).get("total_bytes", 0)
    if host_total < MINIMUM_HOST_MEMORY:
        blockers.append("host memory is below 8 GiB")
    peak = (state.get("memory") or {}).get("peak_bytes")
    if peak is not None and limit and peak / limit >= 0.85:
        blockers.append("worker peak memory is at least 85% of its limit")
    if state.get("host_memory", {}).get("swap_total_bytes", 0) == 0:
        warnings.append("host has no swap; swap is not a substitute for required RAM")
    return {"passed": not blockers, "blockers": blockers, "warnings": warnings, "state": state}


def helper(volume: str, *shell: str) -> str:
    if not SAFE_VOLUME.fullmatch(volume):
        raise ValueError("Unsafe volume name")
    return command(
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "-v", f"{volume}:/source:ro", "alpine:3.20", *shell,
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def snapshot(output: Path) -> tuple[Path, Path]:
    state = inspect()
    volume = state["home_volume"]
    if not volume:
        raise RuntimeError("Persistent /home/node volume is missing")
    if state["running"]:
        raise RuntimeError("Stop the paused worker before taking a migration snapshot")
    helper(volume, "test", "-f", "/source/PAUSED")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output / f"symphony-home-{stamp}.tar.gz"
    partial = output / f".{archive.name}.partial"
    subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "-v", f"{volume}:/source:ro", "-v", f"{output}:/backup",
        "alpine:3.20", "tar", "-C", "/source", "-czf", f"/backup/{partial.name}", ".",
    ], check=True)
    partial.replace(archive)
    members = helper(volume, "sh", "-c", "find /source/workspaces -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null || true")
    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": state,
        "archive": archive.name,
        "archive_sha256": digest(archive),
        "workspace_names": sorted(Path(line).name for line in members.splitlines() if line),
    }
    manifest = archive.with_suffix(archive.suffix + ".json")
    manifest.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return archive, manifest


def verify(archive: Path, manifest: Path | None) -> dict:
    archive = archive.resolve()
    manifest = manifest.resolve() if manifest else archive.with_suffix(archive.suffix + ".json")
    metadata = json.loads(manifest.read_text())
    if metadata["archive_sha256"] != digest(archive):
        raise RuntimeError("Archive checksum does not match its manifest")
    with tarfile.open(archive, "r:gz") as bundle:
        names = [member.name.removeprefix("./") for member in bundle.getmembers()]
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Archive contains an unsafe path")
    if not any(name == "workspaces" or name.startswith("workspaces/") for name in names):
        raise RuntimeError("Archive does not contain the persistent workspaces directory")
    return {"status": "verified", "archive": archive.name, "members": len(names), "sha256": digest(archive)}


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    commands.add_parser("preflight")
    snapshot_parser = commands.add_parser("snapshot")
    snapshot_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("archive", type=Path)
    verify_parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect()
    elif args.command == "preflight":
        result = preflight(inspect())
    elif args.command == "snapshot":
        archive, manifest = snapshot(args.output)
        result = {"archive": str(archive), "manifest": str(manifest)}
    else:
        result = verify(args.archive, args.manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command == "preflight" and not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
