#!/bin/bash
# Called by GitHub Actions on every push to main.
# Pulls the latest infra config and image, runs migrations, restarts services.
set -euo pipefail

DEPLOY_DIR=/opt/launch-lms
STATE_DIR="${DEPLOY_DIR}/.deploy-state"
LOCK_FILE="${DEPLOY_DIR}/.deploy.lock"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another deploy is already running. Exiting."
  exit 1
fi

cd "$DEPLOY_DIR"
git fetch origin
git reset --hard origin/main

mkdir -p "${STATE_DIR}"
source "${DEPLOY_DIR}/scripts/load-release-env.sh"

# Regenerate processed Caddyfile from template in case it changed
DOMAIN=$(grep '^LAUNCHLMS_DOMAIN=' "$DEPLOY_DIR/.env" | cut -d= -f2)
sed "s/your.domain.com/$DOMAIN/g" "$DEPLOY_DIR/Caddyfile" > "$DEPLOY_DIR/Caddyfile.active"

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
fi

docker pull "${LAUNCHLMS_IMAGE}"
docker compose up -d db redis
if docker compose run --rm migrate; then
  cat > "${STATE_DIR}/last-migration.json" <<EOF
{"status":"success","image":"${LAUNCHLMS_IMAGE}","version":"${LAUNCHLMS_RELEASE_VERSION}","commit_sha":"${LAUNCHLMS_RELEASE_COMMIT_SHA}","finished_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
else
  cat > "${STATE_DIR}/last-migration.json" <<EOF
{"status":"failed","image":"${LAUNCHLMS_IMAGE}","version":"${LAUNCHLMS_RELEASE_VERSION}","commit_sha":"${LAUNCHLMS_RELEASE_COMMIT_SHA}","finished_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
  exit 1
fi
docker compose rm -sf launch-lms || true
docker compose up -d --remove-orphans launch-lms caddy
"${DEPLOY_DIR}/scripts/verify-deploy.sh"

cat > "${STATE_DIR}/deployed-release.json" <<EOF
{"image":"${LAUNCHLMS_IMAGE}","version":"${LAUNCHLMS_RELEASE_VERSION}","commit_sha":"${LAUNCHLMS_RELEASE_COMMIT_SHA}","deployed_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  docker logout ghcr.io
fi

docker image prune -f

echo "Deploy complete."
