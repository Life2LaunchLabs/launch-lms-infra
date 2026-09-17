#!/usr/bin/env python3
"""Interactively prepare and upload the two protected operations environment files.

Run this on an operator workstation. Secret values are read from the terminal and
sent to GitHub Environment secrets over gh's stdin; they are never printed or
written to disk by this script.
"""

from __future__ import annotations

from getpass import getpass
import importlib.util
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from environment_topology import load_topology  # noqa: E402

spec = importlib.util.spec_from_file_location("control_plane_env", ROOT / "scripts/validate-control-plane-env.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def plain(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} is required on one line")
    return value


def hidden(label: str) -> str:
    value = getpass(f"{label}: ").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"{label} is required on one line")
    return value


def dotenv(values: dict[str, str]) -> str:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Environment values must occupy one line")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def main() -> None:
    print("Values remain on this workstation and are uploaded directly to GitHub.")
    print("The GitHub App private-key PEM must already be saved in a local file.")
    key_path = Path(plain("GitHub App private-key PEM file path")).expanduser()
    key = key_path.read_text(encoding="utf-8").strip()
    if not key.startswith("-----BEGIN") or not (key.endswith("-----END RSA PRIVATE KEY-----") or key.endswith("-----END PRIVATE KEY-----")):
        raise ValueError("The selected file is not a PEM private key")
    database_password = secrets.token_urlsafe(36)
    postgres = {"POSTGRES_USER": "operations", "POSTGRES_DB": "operations", "POSTGRES_PASSWORD": database_password}
    control = {
        "OPERATIONS_DATABASE_URL": f"postgresql+psycopg://operations:{database_password}@postgres/operations",
        "OPERATIONS_PUBLIC_URL": "https://life2launch.dev",
        "OPERATIONS_DOMAIN": "life2launch.dev",
        "OPERATIONS_EXPECTED_IP": "143.110.225.231",
        "OPERATIONS_ENVIRONMENT": "production",
        "OPERATIONS_READ_ONLY": "true",
        "OPERATIONS_SESSION_SECRET": secrets.token_urlsafe(48),
        "GITHUB_OAUTH_CLIENT_ID": plain("OAuth App client ID"),
        "GITHUB_OAUTH_CLIENT_SECRET": hidden("OAuth App client secret"),
        "GITHUB_APP_ID": plain("GitHub App ID"),
        "GITHUB_APP_INSTALLATION_ID": plain("GitHub App installation ID"),
        "GITHUB_APP_PRIVATE_KEY_FILE": "/run/secrets/github-app-private-key",
        "GITHUB_ALLOWED_ORG": "Life2LaunchLabs",
        "GITHUB_ALLOWED_REPO": "Life2LaunchLabs/launch-lms",
        "SYMPHONY_STATUS_URL": "http://symphony:8788/api/v1/state",
        "JIRA_BASE_URL": plain("Jira base URL, including /ex/jira/<cloudId> for scoped tokens"),
        "JIRA_DELIVERY_EMAIL": plain("BOT delivery Jira account email"),
        "JIRA_DELIVERY_TOKEN": hidden("BOT delivery Jira token"),
        "JIRA_FEEDBACK_EMAIL": plain("FEED feedback Jira account email"),
        "JIRA_FEEDBACK_TOKEN": hidden("FEED feedback Jira token"),
    }
    module.validate(control, postgres, topology=load_topology(ROOT / "deploy/environments/launch-lms.yaml"),
                    check_dns=False)
    answer = input("Upload both files to the protected operations environment? Type UPLOAD: ").strip()
    if answer != "UPLOAD":
        raise SystemExit("No secrets were uploaded")
    repository = "Life2LaunchLabs/launch-lms-infra"
    for name, values in (("OPERATIONS_POSTGRES_ENV", postgres), ("OPERATIONS_CONTROL_PLANE_ENV", control)):
        subprocess.run(["gh", "secret", "set", name, "--repo", repository, "--env", "operations"],
                       input=dotenv(values), text=True, check=True, stdout=subprocess.DEVNULL)
        print(f"Uploaded {name}.")
    subprocess.run(["gh", "secret", "set", "OPERATIONS_GITHUB_APP_PRIVATE_KEY", "--repo", repository,
                    "--env", "operations"], input=key + "\n", text=True, check=True,
                   stdout=subprocess.DEVNULL)
    print("Uploaded OPERATIONS_GITHUB_APP_PRIVATE_KEY.")
    print("Keep the PEM in your password manager or protected local vault; do not commit it.")


if __name__ == "__main__":
    main()
