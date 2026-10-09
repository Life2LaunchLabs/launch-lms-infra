#!/usr/bin/env bash
# Merge work authored on unstable into production. Dry run unless --apply.
#   --base      the production snapshot unstable was last refreshed from
#   --unstable  a snapshot taken on unstable with snapshot.sh --promotion-source
# Remaining options go to promote-unstable.py (--apply, --apply-deletes,
# --allow-new-users, --include-learner-activity, --prefer-unstable TABLE, ...).
set -euo pipefail
usage='Usage: promote-unstable.sh --base /abs/refresh-snapshot --unstable /abs/unstable-snapshot [--apply] [options]'
base='' unstable='' apply=false passthrough=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) base=$2; shift 2 ;;
    --unstable) unstable=$2; shift 2 ;;
    --apply) apply=true; passthrough+=("$1"); shift ;;
    *) passthrough+=("$1"); shift ;;
  esac
done
[[ "$base" == /* && "$unstable" == /* ]] || { echo "$usage"; exit 1; }
cd "$(dirname "$0")/.."
exec 9>.deploy.lock
flock -n 9 || { echo 'Another operation holds the deployment lock'; exit 1; }
source scripts/load-release-env.sh
[[ "$DEPLOY_ENVIRONMENT" == production ]] || { echo 'Promotion runs on production only'; exit 1; }
python3 scripts/check-environment.py
python3 scripts/render-app-env.py
stamp=$(date -u +%Y%m%d%H%M%S)
work=$(mktemp -d /var/tmp/launch-promote.XXXXXX)
report_dir="$PWD/.deploy-state/promotion-$stamp"
umask 077
mkdir -p "$report_dir" "$work/content"
# Validates both snapshots and prints: base_url unstable_url domain-arguments...
plan=$(python3 - "$base" "$unstable" "$stamp" "$work/content" <<'PY'
import hashlib, json, sys, tarfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
sys.path.insert(0, 'scripts')
from env_file import read_env
base, unstable, stamp, content = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
env = read_env(Path('.env'))
metas = {}
for label, path in (('base', base), ('unstable', unstable)):
    meta = json.loads((path/'snapshot.json').read_text())
    for name in ('database.dump', 'content.tar.gz', 'release.json'):
        with (path/name).open('rb') as file:
            assert hashlib.file_digest(file, 'sha256').hexdigest() == meta['files'][name], f'{label} snapshot checksum mismatch: {name}'
    metas[label] = meta
assert metas['base']['source_domain'] == env['LAUNCHLMS_DOMAIN'], 'The base must be a snapshot of this production installation'
assert metas['unstable'].get('environment') == 'unstable', 'Take the unstable side with snapshot.sh --promotion-source on unstable'
assert metas['base']['created_at'] < metas['unstable']['created_at'], 'The unstable snapshot must be newer than its refresh base'
with tarfile.open(unstable/'content.tar.gz') as archive:
    members = archive.getmembers()
    for member in members:
        assert not member.name.startswith('/') and '..' not in Path(member.name).parts
        assert member.isfile() or member.isdir(), 'Snapshot archive must contain only regular files/directories'
    archive.extractall(content, members=members, filter='data')
url = urlsplit(env['LAUNCHLMS_SQL_CONNECTION_STRING'])
assert url.hostname == 'db' and url.path == '/launchlms', 'Promotion requires the local Compose production database'
database = lambda name: urlunsplit(url._replace(path='/'+name))
domains = [metas['unstable']['source_domain'], *metas['unstable'].get('legacy_domains', [])]
print(database(f'launchlms_promote_base_{stamp}'), database(f'launchlms_promote_unstable_{stamp}'),
      *[arg for domain in domains for arg in ('--unstable-domain', domain)],
      '--production-domain', env['LAUNCHLMS_DOMAIN'])
PY
)
read -r -a plan_args <<< "$plan"
base_url=${plan_args[0]} unstable_url=${plan_args[1]} domain_args=("${plan_args[@]:2}")
base_db=launchlms_promote_base_$stamp unstable_db=launchlms_promote_unstable_$stamp
paused=false
cleanup() {
  if [[ "$paused" == true ]]; then docker compose start launch-lms caddy; fi
  docker compose exec -T db dropdb -U launchlms --if-exists "$base_db" || true
  docker compose exec -T db dropdb -U launchlms --if-exists "$unstable_db" || true
  rm -rf -- "$work"
}
trap cleanup EXIT
for pair in "$base_db:$base" "$unstable_db:$unstable"; do
  docker compose exec -T db createdb -U launchlms "${pair%%:*}"
  docker compose exec -T db pg_restore -U launchlms --no-owner --no-acl --exit-on-error -d "${pair%%:*}" < "${pair#*:}/database.dump"
done
# Bring both copies to this release's schema. Unstable must not be ahead of it.
docker compose run --rm -T -e LAUNCHLMS_SQL_CONNECTION_STRING="$base_url" migrate
docker compose run --rm -T -e LAUNCHLMS_SQL_CONNECTION_STRING="$unstable_url" migrate
if [[ "$apply" == true ]]; then
  recovery="/root/launch-snapshots/pre-promotion-$stamp"
  mkdir -p "$recovery"
  docker compose stop caddy launch-lms
  paused=true
  docker compose exec -T db pg_dump -U launchlms -d launchlms -Fc > "$recovery/database.dump"
  docker compose run --rm --no-deps -T --entrypoint tar migrate -C /app/api/content -czf - . > "$recovery/content.tar.gz"
  echo "Recovery point: $recovery"
fi
docker compose run --rm --no-deps -T \
  -v "$work/content:/promote-content:ro" -v "$report_dir:/promote-report" \
  --entrypoint sh migrate -lc 'cd /app/api && uv run python - "$@"' promote \
  --base "$base_url" --unstable "$unstable_url" "${domain_args[@]}" \
  --content-source /promote-content --content-target /app/api/content \
  --report /promote-report/report.json "${passthrough[@]}" < scripts/promote-unstable.py
if [[ "$apply" == true ]]; then
  docker compose start launch-lms caddy
  paused=false
  bash scripts/verify-deploy.sh
  # New or edited resources need search documents; the backfill is incremental.
  if docker compose exec -T launch-lms test -f /app/api/scripts/backfill_resource_search.py; then
    docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python scripts/backfill_resource_search.py'
  fi
fi
echo "Report: $report_dir/report.json"
