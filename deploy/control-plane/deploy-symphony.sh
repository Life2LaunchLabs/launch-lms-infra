#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
test -s /etc/launch-operations/managed-host.json
test -s /etc/launch-symphony/runner.env
test -s /etc/launch-symphony/openai-api-key
test "$(stat -c %a /etc/launch-symphony/runner.env)" = 600
test "$(stat -c %a /etc/launch-symphony/openai-api-key)" = 600

compose=(docker compose -f docker-compose.symphony.yml -f deploy/control-plane/symphony.override.yml)
docker network inspect launch-operations-status >/dev/null 2>&1 || docker network create launch-operations-status >/dev/null
first_start=false
if [[ -z "$("${compose[@]}" ps -aq symphony)" ]]; then
  first_start=true
fi

"${compose[@]}" build symphony
if "$first_start"; then
  # A restored volume can lack PAUSED; no first start may dispatch by accident.
  "${compose[@]}" run --rm --no-deps --entrypoint /bin/sh symphony -c 'touch /home/node/PAUSED'
fi
"${compose[@]}" up -d --no-build --wait --wait-timeout 240
curl --fail --silent --show-error http://127.0.0.1:8788/api/v1/state >/dev/null
