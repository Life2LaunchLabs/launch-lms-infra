#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
test -s /etc/launch-operations/managed-host.json
test "$(stat -c %a /etc/launch-operations/control-plane.env)" = 600
test "$(stat -c %a /etc/launch-operations/postgres.env)" = 600
compose=(docker compose -f deploy/control-plane/compose.yaml)
test -z "$("${compose[@]}" ps -q caddy)" || { echo 'Public Caddy is running during private stage' >&2; exit 1; }
python3 scripts/validate-control-plane-env.py \
  /etc/launch-operations/control-plane.env /etc/launch-operations/postgres.env \
  --topology deploy/environments/launch-lms.yaml --skip-dns

docker network inspect launch-operations-status >/dev/null 2>&1 || docker network create launch-operations-status >/dev/null
worker="$(docker compose -f docker-compose.symphony.yml -f deploy/control-plane/symphony.override.yml ps -q symphony)"
test -n "$worker" || { echo 'Symphony worker is not running' >&2; exit 1; }
if ! docker inspect -f '{{json .NetworkSettings.Networks}}' "$worker" | grep -q 'launch-operations-status'; then
  docker network connect --alias symphony launch-operations-status "$worker"
fi

"${compose[@]}" build control-plane-api control-plane-web
"${compose[@]}" up -d --wait --wait-timeout 240 postgres
"${compose[@]}" run --rm --no-deps control-plane-api python migrate.py
"${compose[@]}" up -d --no-build --wait --wait-timeout 240 control-plane-api control-plane-web
"${compose[@]}" exec -T control-plane-api python - <<'PY'
import os
import urllib.error
import urllib.request

urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)
urllib.request.urlopen(os.environ['SYMPHONY_STATUS_URL'], timeout=5)
request = urllib.request.Request('http://127.0.0.1:8000/api/v1/embed/session', data=b'{}')
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 403, error.code
else:
    raise AssertionError('Read-only write gate did not reject an embed write')
PY
"${compose[@]}" exec -T control-plane-web wget -qO- http://127.0.0.1:8080/ >/dev/null
echo "Private control-plane stage healthy at $(git rev-parse HEAD)."
