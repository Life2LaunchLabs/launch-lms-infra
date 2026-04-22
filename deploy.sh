#!/bin/bash
# Called by GitHub Actions on every push to main.
# Pulls the latest infra config and image, runs migrations, restarts services.
set -euo pipefail

DEPLOY_DIR=/opt/launch-lms

cd "$DEPLOY_DIR"
git fetch origin
git reset --hard origin/main

# Regenerate processed Caddyfile from template in case it changed
DOMAIN=$(grep '^LAUNCHLMS_DOMAIN=' "$DEPLOY_DIR/.env" | cut -d= -f2)
sed "s/your.domain.com/$DOMAIN/g" "$DEPLOY_DIR/Caddyfile" > "$DEPLOY_DIR/Caddyfile.active"

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
fi

docker compose pull
docker compose up -d db redis
docker compose run --rm migrate
docker compose up -d --build --remove-orphans

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  docker logout ghcr.io
fi

docker image prune -f

echo "Deploy complete."
