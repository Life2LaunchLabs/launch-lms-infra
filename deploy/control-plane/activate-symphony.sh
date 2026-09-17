#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
test -s /etc/launch-operations/managed-host.json
compose=(docker compose -f docker-compose.symphony.yml -f deploy/control-plane/symphony.override.yml)
docker network inspect launch-operations-status >/dev/null 2>&1 || docker network create launch-operations-status >/dev/null
test -n "$("${compose[@]}" ps -aq symphony)"
"${compose[@]}" exec -T symphony test -f /home/node/PAUSED
"${compose[@]}" exec -T symphony mv /home/node/PAUSED /home/node/PAUSED.before-cutover
"${compose[@]}" restart symphony

for attempt in $(seq 1 24); do
  if curl --fail --silent http://127.0.0.1:8788/api/v1/state >/dev/null; then
    break
  fi
  sleep 5
done
curl --fail --silent --show-error http://127.0.0.1:8788/api/v1/state >/dev/null
"${compose[@]}" exec -T symphony test ! -f /home/node/PAUSED
"${compose[@]}" exec -T symphony python3 -c '
import yaml
from pathlib import Path
text = Path("/home/node/WORKFLOW.md").read_text()
frontmatter = yaml.safe_load(text.split("---\n", 2)[1])
assert frontmatter["tracker"]["active_states"], "worker remains paused"
'
echo 'Operations Symphony is healthy and dispatch states are active.'
