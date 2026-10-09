#!/usr/bin/env bash
# Consistent local DB + filesystem snapshot; the app is paused while copying.
# Production snapshots seed unstable refreshes; an unstable snapshot is the
# source for promote-unstable.sh on production.
set -euo pipefail
usage='Usage: snapshot.sh --maintenance-window|--promotion-source /absolute/new/snapshot-directory'
[[ $# == 2 && ( "$1" == --maintenance-window || "$1" == --promotion-source ) ]] || { echo "$usage"; exit 1; }
mode=$1
destination=$2
[[ "$destination" == /* && ! -e "$destination" ]] || { echo 'Use a new absolute destination outside the repository'; exit 1; }
cd "$(dirname "$0")/.."
exec 9>.deploy.lock
flock -n 9
source scripts/load-release-env.sh
if [[ "$mode" == --maintenance-window ]]; then
  [[ "$DEPLOY_ENVIRONMENT" == production ]] || { echo 'Snapshots must originate from production'; exit 1; }
else
  [[ "$DEPLOY_ENVIRONMENT" == unstable ]] || { echo 'Promotion sources must originate from unstable'; exit 1; }
fi
database=$(python3 - <<'PY'
from pathlib import Path
from scripts.env_file import read_env
from urllib.parse import urlparse
import os, re
e=read_env(Path('.env'))
assert e['LAUNCHLMS_CONTENT_DELIVERY_TYPE']=='filesystem', 'S3 requires an independent bucket export; this helper only supports filesystem'
u=urlparse(e['LAUNCHLMS_SQL_CONNECTION_STRING'])
name=u.path.lstrip('/')
allowed='launchlms' if os.environ['DEPLOY_ENVIRONMENT']=='production' else r'launchlms(_refresh_[0-9]+)?'
assert u.hostname=='db' and re.fullmatch(allowed, name), 'This helper requires the local Compose database'
print(name)
PY
)
umask 077
mkdir -p "$destination"
# Restart the same containers even if copying fails. Never apply new config here.
resume() { docker compose start launch-lms caddy; }
trap resume EXIT
docker compose stop caddy launch-lms
docker compose exec -T db pg_dump -U launchlms -d "$database" -Fc > "$destination/database.dump"
docker compose run --rm --no-deps -T --entrypoint tar migrate -C /app/api/content -czf - . > "$destination/content.tar.gz"
cp .deploy-state/deployed-release.json "$destination/release.json"
python3 scripts/snapshot-metadata.py "$destination"
echo "Snapshot completed: $destination (contains private $DEPLOY_ENVIRONMENT data)."
