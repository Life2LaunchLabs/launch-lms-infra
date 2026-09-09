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
docker pull "$LAUNCHLMS_IMAGE"
docker compose build caddy
docker compose up -d --wait db redis embeddings
docker compose exec -T embeddings ollama pull all-minilm:33m
# Existing production image stays running if migrations fail. Schema changes can
# still affect it: back up before incompatible migrations (see README).
docker compose run --rm migrate
if [[ -f .deploy-state/deployed-release.json ]]; then
  cp .deploy-state/deployed-release.json .deploy-state/previous-release.json
fi
docker compose up -d --remove-orphans launch-lms caddy
# Ensure mounted config changes are picked up by an already-running Caddy.
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
bash scripts/verify-deploy.sh
# Backfill only after the embedding model and application are ready. Releases
# created before resource search existed do not contain this script; domain-only
# migration of those pinned images must remain possible without changing images.
if docker compose exec -T launch-lms test -f /app/api/scripts/backfill_resource_search.py; then
  docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python scripts/backfill_resource_search.py'
else
  echo 'Pinned legacy image has no resource-search backfill; skipping.'
fi
cp "$RELEASE_LOCK" .deploy-state/deployed-release.json
echo "Verified $DEPLOY_ENVIRONMENT deployment: $LAUNCHLMS_RELEASE_VERSION ($LAUNCHLMS_RELEASE_COMMIT_SHA)"
