#!/usr/bin/env bash
# Repair uses the same lock, readiness checks, and serialization as deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash deploy.sh
