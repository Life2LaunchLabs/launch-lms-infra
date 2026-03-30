#!/bin/bash
# Called by GitHub Actions on every push to prod.
# Pulls latest image and restarts the container.
set -e

DEPLOY_DIR=/opt/learnhouse

cd "$DEPLOY_DIR"
git pull
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f

echo "Deploy complete."
