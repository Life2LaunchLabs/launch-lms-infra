#!/usr/bin/env python3
"""Render validated, private runtime files from individual GitHub secrets."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
from pathlib import Path

from environment_topology import load_topology

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "control_plane_env", ROOT / "scripts/validate-control-plane-env.py"
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

SECRET_MAP = {
    "OPERATIONS_SESSION_SECRET": "OPERATIONS_SESSION_SECRET",
    "GITHUB_OAUTH_CLIENT_ID": "OPERATIONS_GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_OAUTH_CLIENT_SECRET": "OPERATIONS_GITHUB_OAUTH_CLIENT_SECRET",
    "GITHUB_APP_ID": "OPERATIONS_GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID": "OPERATIONS_GITHUB_APP_INSTALLATION_ID",
    "JIRA_BASE_URL": "OPERATIONS_JIRA_BASE_URL",
    "JIRA_DELIVERY_EMAIL": "OPERATIONS_JIRA_DELIVERY_EMAIL",
    "JIRA_DELIVERY_TOKEN": "OPERATIONS_JIRA_DELIVERY_TOKEN",
    "JIRA_FEEDBACK_EMAIL": "OPERATIONS_JIRA_FEEDBACK_EMAIL",
    "JIRA_FEEDBACK_TOKEN": "OPERATIONS_JIRA_FEEDBACK_TOKEN",
}


def required(source: dict[str, str], name: str) -> str:
    value = source.get(name, "").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} is missing or contains multiple lines")
    return value


def runtime(source: dict[str, str]) -> tuple[dict[str, str], dict[str, str], str]:
    password = required(source, "OPERATIONS_POSTGRES_PASSWORD")
    if len(password) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", password):
        raise ValueError("OPERATIONS_POSTGRES_PASSWORD must be at least 32 URL-safe characters")
    key = source.get("OPERATIONS_GITHUB_APP_PRIVATE_KEY", "").strip()
    if not any(key.startswith(f"-----BEGIN {kind}-----") and
               key.endswith(f"-----END {kind}-----") for kind in ("RSA PRIVATE KEY", "PRIVATE KEY")):
        raise ValueError("OPERATIONS_GITHUB_APP_PRIVATE_KEY must be a PEM private key")
    topology = load_topology(ROOT / "deploy/environments/launch-lms.yaml")
    url = topology["operations"]["public_url"]
    control = {
        "OPERATIONS_DATABASE_URL": f"postgresql+psycopg://operations:{password}@postgres/operations",
        "OPERATIONS_PUBLIC_URL": url,
        "OPERATIONS_DOMAIN": url.removeprefix("https://"),
        "OPERATIONS_EXPECTED_IP": topology["operations"]["expected_ipv4"],
        "OPERATIONS_ENVIRONMENT": "production",
        "OPERATIONS_READ_ONLY": "true",
        "GITHUB_APP_PRIVATE_KEY_FILE": "/run/secrets/github-app-private-key",
        "GITHUB_ALLOWED_ORG": "Life2LaunchLabs",
        "GITHUB_ALLOWED_REPO": "Life2LaunchLabs/launch-lms",
        "SYMPHONY_STATUS_URL": "http://symphony:8788/api/v1/state",
    }
    control.update({name: required(source, secret) for name, secret in SECRET_MAP.items()})
    postgres = {"POSTGRES_USER": "operations", "POSTGRES_DB": "operations", "POSTGRES_PASSWORD": password}
    validator.validate(control, postgres, topology=topology, check_dns=False)
    return control, postgres, key


def render(destination: Path, source: dict[str, str]) -> None:
    control, postgres, key = runtime(source)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, values in (("control-plane.env", control), ("postgres.env", postgres)):
        path = destination / name
        path.write_text("".join(f"{field}={value}\n" for field, value in values.items()), encoding="utf-8")
        path.chmod(0o600)
    path = destination / "github-app.pem"
    path.write_text(key + "\n", encoding="utf-8")
    path.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        render(args.destination, dict(os.environ))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print("Validated private runtime files are ready.")
