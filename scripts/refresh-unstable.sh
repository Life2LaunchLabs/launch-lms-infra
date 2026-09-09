#!/usr/bin/env bash
# Restores into a fresh DB/volume. Old DB/volume remain available for recovery.
set -euo pipefail
[[ $# == 2 && "$1" == --replace-test-data ]] || { echo 'Usage: refresh-unstable.sh --replace-test-data /absolute/snapshot-directory'; exit 1; }
snapshot=$2
[[ "$snapshot" == /* ]] || exit 1
cd "$(dirname "$0")/.."
exec 9>.deploy.lock
flock -n 9
source scripts/load-release-env.sh
[[ "$DEPLOY_ENVIRONMENT" == unstable ]] || { echo 'Refusing to refresh production'; exit 1; }
python3 scripts/check-environment.py
stamp=$(date -u +%Y%m%d%H%M%S)
python3 scripts/prepare-refresh.py "$snapshot" "$stamp"
python3 scripts/render-app-env.py .deploy-state/refresh.env .deploy-state/refresh-app.env
read_plan() { python3 -c 'import json,sys; print(json.load(open(".deploy-state/refresh-plan.json"))[sys.argv[1]])' "$1"; }
database=$(read_plan database)
volume=$(read_plan volume)
source_domain=$(read_plan source_domain)
target_domain=$(read_plan target_domain)
candidate_compose() {
  docker compose --env-file .deploy-state/refresh.env -f docker-compose.yml -f docker-compose.unstable.yml -f .deploy-state/refresh.compose.json "$@"
}
docker compose exec -T db createdb -U launchlms "$database"
docker volume create "$volume" >/dev/null
docker compose exec -T db pg_restore -U launchlms --no-owner --no-acl --exit-on-error -d "$database" < "$snapshot/database.dump"
candidate_compose run --rm --no-deps -T --entrypoint tar migrate -C /app/api/content -xzf - < "$snapshot/content.tar.gz"
candidate_compose run --rm -T migrate
candidate_compose run --rm --no-deps -T -e SOURCE_DOMAIN="$source_domain" -e TARGET_DOMAIN="$target_domain" --entrypoint sh migrate -lc 'cd /app/api && uv run python -' < scripts/sanitize-copy.py
# The old app has stayed available while restore/migrations/sanitization ran.
# Cut over only now, retaining its env and data for explicit recovery.
cp .env ".deploy-state/before-refresh-$stamp.env"
switched=false
recover() {
  if [[ "$switched" == true ]]; then
    cp ".deploy-state/before-refresh-$stamp.env" .env
    python3 scripts/render-app-env.py
    docker compose up -d launch-lms caddy
    echo 'Refresh failed; previous environment restored. Fresh DB/volume retained for diagnosis.' >&2
  fi
}
trap recover ERR
docker compose stop caddy launch-lms
switched=true
cp .deploy-state/refresh.env .env
# Clear only the test Redis instance; production Redis is never contacted/copied.
docker compose exec -T redis redis-cli FLUSHALL >/dev/null
export DEPLOY_LOCK_HELD=true
bash deploy.sh
cp .deploy-state/refresh-plan.json .deploy-state/last-refresh.json
switched=false
trap - ERR
echo 'Refresh complete. Test changes were replaced; testers must log in again.'
echo "Previous env: .deploy-state/before-refresh-$stamp.env (old DB/content retained)"
