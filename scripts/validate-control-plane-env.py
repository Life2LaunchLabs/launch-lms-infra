#!/usr/bin/env python3
"""Validate control-plane runtime files without exposing their values."""

from __future__ import annotations

import argparse
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

from env_file import read_env

REQUIRED = (
    "OPERATIONS_DATABASE_URL", "OPERATIONS_PUBLIC_URL", "OPERATIONS_DOMAIN",
    "OPERATIONS_EXPECTED_IP", "OPERATIONS_SESSION_SECRET", "GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY", "GITHUB_ALLOWED_ORG", "GITHUB_ALLOWED_REPO",
    "JIRA_BASE_URL", "JIRA_DELIVERY_EMAIL", "JIRA_DELIVERY_TOKEN",
    "JIRA_FEEDBACK_EMAIL", "JIRA_FEEDBACK_TOKEN",
)


def validate(control: dict[str, str], postgres: dict[str, str], resolver=socket.getaddrinfo) -> None:
    missing = [name for name in REQUIRED if not control.get(name) or
               "CONFIGURE" in control[name] or "CHANGE_ME" in control[name]]
    for name in ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD"):
        if not postgres.get(name) or "CHANGE_ME" in postgres[name] or "GENERATE" in postgres[name]:
            missing.append(name)
    if missing:
        raise ValueError("Missing runtime configuration: " + ", ".join(missing))
    if len(control["OPERATIONS_SESSION_SECRET"]) < 32:
        raise ValueError("OPERATIONS_SESSION_SECRET must contain at least 32 characters")
    if control.get("OPERATIONS_ENVIRONMENT") != "production":
        raise ValueError("The dedicated control plane must use OPERATIONS_ENVIRONMENT=production")
    public = urlparse(control["OPERATIONS_PUBLIC_URL"])
    if public.scheme != "https" or public.hostname != control["OPERATIONS_DOMAIN"] or public.path not in ("", "/"):
        raise ValueError("Public URL must be the HTTPS operations domain without a path")
    expected = str(ipaddress.ip_address(control["OPERATIONS_EXPECTED_IP"]))
    database = urlparse(control["OPERATIONS_DATABASE_URL"])
    if database.scheme != "postgresql+psycopg" or database.hostname != "postgres":
        raise ValueError("Database URL must target the private Compose PostgreSQL service")
    if database.path != f"/{postgres['POSTGRES_DB']}" or database.username != postgres["POSTGRES_USER"]:
        raise ValueError("Database URL and PostgreSQL identity do not match")
    if database.password != postgres["POSTGRES_PASSWORD"]:
        raise ValueError("Database URL and PostgreSQL password do not match")
    if control["JIRA_DELIVERY_TOKEN"] == control["JIRA_FEEDBACK_TOKEN"]:
        raise ValueError("Delivery and feedback Jira credentials must be distinct")
    addresses = {value[4][0] for value in resolver(control["OPERATIONS_DOMAIN"], 443, type=socket.SOCK_STREAM)}
    if expected not in addresses:
        raise ValueError("Operations DNS does not resolve to OPERATIONS_EXPECTED_IP")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("control", type=Path)
    parser.add_argument("postgres", type=Path)
    args = parser.parse_args()
    try:
        validate(read_env(args.control), read_env(args.postgres))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print("Control-plane runtime contract is valid.")


if __name__ == "__main__":
    main()
