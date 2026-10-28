#!/usr/bin/env bash
# Deploy the already checked-out infra revision. No moving-branch git reset.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .deploy-state
if [[ "${DEPLOY_LOCK_HELD:-false}" != true ]]; then
  exec 9>.deploy.lock
  flock -n 9 || { echo 'Another operation holds the deployment lock'; exit 1; }
fi
source scripts/load-release-env.sh
python3 scripts/check-environment.py
python3 scripts/render-app-env.py
cleanup() {
  if [[ -n "${DOCKER_CONFIG_TEMP:-}" ]]; then rm -rf -- "$DOCKER_CONFIG_TEMP"; fi
}
trap cleanup EXIT
if [[ -n "${GHCR_TOKEN:-}" ]]; then
  DOCKER_CONFIG_TEMP=$(mktemp -d)
  export DOCKER_CONFIG="$DOCKER_CONFIG_TEMP"
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USERNAME:?}" --password-stdin
fi
python3 scripts/render-caddy.py
cp "$RELEASE_LOCK" .deploy-state/attempted-release.json
# Every candidate is a new pinned image, and old ones otherwise pile up until a
# pull fails with "no space left on device". Remove images no container uses (the
# running release keeps its own); a rollback pulls its pinned image again.
docker image prune -af
docker builder prune -f
if ! docker pull "$LAUNCHLMS_IMAGE"; then
  # A bootstrapped legacy host may no longer have registry credentials. A local
  # image is acceptable only under the validated digest reference; verification
  # below still checks its image ID, embedded commit, and schema.
  docker image inspect "$LAUNCHLMS_IMAGE" >/dev/null || {
    echo 'Pinned image is unavailable locally and could not be pulled.' >&2
    exit 1
  }
  echo 'Registry pull failed; using the existing exact pinned image.'
fi
docker compose build caddy
docker compose up -d --wait db redis embeddings
docker compose exec -T embeddings ollama pull all-minilm:33m
# Stop the app while migrating: a migration that alters a table the running app
# holds open waits on its lock and queues every query on that table behind it,
# which freezes the site. If migrations fail, the previous release starts again
# on the schema the failed migration rolled back to. Schema changes can still
# affect it: back up before incompatible migrations (see README).
docker compose stop launch-lms
if ! docker compose run --rm migrate; then
  echo 'Migrations failed; starting the previous release again.' >&2
  docker compose start launch-lms || true
  exit 1
fi
if [[ -f .deploy-state/deployed-release.json ]]; then
  cp .deploy-state/deployed-release.json .deploy-state/previous-release.json
fi
docker compose up -d --remove-orphans launch-lms caddy
# Ensure mounted config changes are picked up by an already-running Caddy.
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
bash scripts/verify-deploy.sh
python3 scripts/verify-domain-preflight.py
# Backfill only after the embedding model and application are ready. Releases
# created before resource search existed do not contain this script; domain-only
# migration of those pinned images must remain possible without changing images.
if docker compose exec -T launch-lms test -f /app/api/scripts/backfill_resource_search.py; then
  docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python scripts/backfill_resource_search.py'
else
  echo 'Pinned legacy image has no resource-search backfill; skipping.'
fi
# One-time upgrade steps. Each runs once per host after a release that has it
# and leaves a marker in .deploy-state; delete the marker to run it again.
# Copy uploads from the content volume into the bucket once storage is s3api
# (files already in the bucket are skipped).
if [[ ! -f .deploy-state/content-synced-to-s3 ]] \
  && grep -Eq "^LAUNCHLMS_CONTENT_DELIVERY_TYPE='?s3api'?$" .deploy-state/app.env \
  && docker compose exec -T launch-lms test -f /app/api/scripts/sync_content_to_s3.py; then
  if docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python scripts/sync_content_to_s3.py'; then
    touch .deploy-state/content-synced-to-s3
  else
    echo 'Copying uploads to the bucket failed; the next deploy tries again.' >&2
  fi
fi
# Move activities to sequences and version 3 pages. What it replaces is kept,
# and `cli.py migrate-learning-content --restore` puts it back.
if [[ ! -f .deploy-state/learning-content-migrated ]] \
  && docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python cli.py migrate-learning-content --help' >/dev/null 2>&1; then
  if docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python cli.py migrate-learning-content --apply'; then
    touch .deploy-state/learning-content-migrated
  else
    echo 'Activity content migration failed; the next deploy tries again.' >&2
  fi
fi
cp "$RELEASE_LOCK" .deploy-state/deployed-release.json
echo "Verified $DEPLOY_ENVIRONMENT deployment: $LAUNCHLMS_RELEASE_VERSION ($LAUNCHLMS_RELEASE_COMMIT_SHA)"

if [[ "$DEPLOY_ENVIRONMENT" == unstable && -f /etc/launch-symphony/ENABLED ]]; then
  bash scripts/deploy-symphony.sh
fi
