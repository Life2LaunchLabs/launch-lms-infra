#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "$(cat .deployment-environment)" == unstable ]] || { echo 'Symphony is dev-only' >&2; exit 1; }
[[ -f /etc/launch-symphony/ENABLED ]] || { echo 'Symphony not enabled on this host'; exit 0; }
test -s /etc/launch-symphony/runner.env
test -s /etc/launch-symphony/openai-api-key
# Never inherit the application's Compose project, env, or private network.
compose=(docker compose)
if [[ -f /etc/launch-symphony/compose.env ]]; then
  compose+=(--env-file /etc/launch-symphony/compose.env)
fi
env -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME "${compose[@]}" -f docker-compose.symphony.yml up -d --build --wait --wait-timeout 240
