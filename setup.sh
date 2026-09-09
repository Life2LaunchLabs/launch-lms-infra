#!/usr/bin/env bash
# Run from the checked-out infra repo on a fresh host after installing Docker.
set -euo pipefail
cd "$(dirname "$0")"
[[ "$PWD" == /opt/launch-lms ]] || { echo 'Clone this repo into /opt/launch-lms first (see README).'; exit 1; }
[[ ! -e .env && ! -e .deployment-environment ]] || { echo 'Already initialized; use deploy.sh (see README for migration).'; exit 1; }
command -v docker >/dev/null
docker compose version >/dev/null
python3 scripts/setup-environment.py "$@"
exec bash deploy.sh
