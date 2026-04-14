#!/bin/bash
# Called by GitHub Actions on every push to prod.
# Pulls the latest image, runs migrations, and only then restarts the app.
set -euo pipefail

DEPLOY_DIR=/opt/launch-lms

cd "$DEPLOY_DIR"
git fetch origin
git reset --hard origin/main

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
fi

docker compose pull
docker compose up -d db redis
docker compose run --rm migrate
docker compose up -d --remove-orphans launch-lms

if [ -n "${GHCR_TOKEN:-}" ] && [ -n "${GHCR_USERNAME:-}" ]; then
  docker logout ghcr.io
fi

docker image prune -f

echo "Deploy complete."
