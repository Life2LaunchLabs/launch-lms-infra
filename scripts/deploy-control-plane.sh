#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f /etc/launch-operations/managed-host.json ]] || { echo 'Host is not repository-managed' >&2; exit 1; }
[[ -s /etc/launch-operations/control-plane.env ]] || { echo 'Missing control-plane environment' >&2; exit 1; }
[[ -s /etc/launch-operations/postgres.env ]] || { echo 'Missing PostgreSQL environment' >&2; exit 1; }
[[ "$(stat -c '%a' /etc/launch-operations/control-plane.env)" == 600 ]] || { echo 'Unsafe control-plane environment mode' >&2; exit 1; }
[[ "$(stat -c '%a' /etc/launch-operations/postgres.env)" == 600 ]] || { echo 'Unsafe PostgreSQL environment mode' >&2; exit 1; }

python3 scripts/validate-control-plane-env.py \
  /etc/launch-operations/control-plane.env /etc/launch-operations/postgres.env \
  --topology deploy/environments/launch-lms.yaml --require-operations-cutover

docker compose -f deploy/control-plane/compose.yaml config --quiet
docker network inspect launch-operations-status >/dev/null
docker compose -f deploy/control-plane/compose.yaml up -d --wait --wait-timeout 240 postgres
docker compose -f deploy/control-plane/compose.yaml run --rm --build --no-deps control-plane-api python migrate.py
docker compose -f deploy/control-plane/compose.yaml up -d --build --wait --wait-timeout 300

domain="$(python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from env_file import read_env
print(read_env(Path('/etc/launch-operations/control-plane.env'))['OPERATIONS_DOMAIN'])
PY
)"
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 "https://${domain}/" >/dev/null

install -d -m 0700 /var/lib/launch-operations
python3 - "$domain" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
value = {"schema_version": 1, "revision": revision, "domain": sys.argv[1],
         "deployed_at": datetime.now(timezone.utc).isoformat()}
Path("/var/lib/launch-operations/deployed.json").write_text(json.dumps(value, indent=2) + "\n")
PY
chmod 0600 /var/lib/launch-operations/deployed.json
echo "Control plane deployed and verified at revision $(git rev-parse HEAD)."
