#!/bin/bash
# Called by GitHub Actions on every push to prod.
# Pulls latest image and restarts the container.
set -e

DEPLOY_DIR=/opt/launch-lms

cd "$DEPLOY_DIR"
git fetch origin
git reset --hard origin/main
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f

echo "Deploy complete."
