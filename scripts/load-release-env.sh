#!/usr/bin/env bash
# Source from deploy/repair/verification. Never source .env as shell code.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENVIRONMENT=$(cat "${DEPLOY_DIR}/.deployment-environment")
case "$DEPLOY_ENVIRONMENT" in
  production) RELEASE_LOCK="$DEPLOY_DIR/release.lock.json" ;;
  unstable) RELEASE_LOCK="$DEPLOY_DIR/.deploy-state/unstable.lock.json" ;;
  *) echo 'Initialize .deployment-environment as production or unstable' >&2; return 1 ;;
esac
export DEPLOY_ENVIRONMENT
# Release values are validated and shell-quoted; no credentials are emitted.
release_exports=$(python3 "$SCRIPT_DIR/release-env.py" "$RELEASE_LOCK" "$DEPLOY_ENVIRONMENT")
eval "$release_exports"
export COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
if [[ "$DEPLOY_ENVIRONMENT" == unstable ]]; then
  export COMPOSE_FILE="$COMPOSE_FILE:$DEPLOY_DIR/docker-compose.unstable.yml"
fi
