#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

source "${SCRIPT_DIR}/load-release-env.sh"

RESTART_ONLY=false
REPULL=false

for arg in "$@"; do
  case "${arg}" in
    --restart-only) RESTART_ONLY=true ;;
    --repull) REPULL=true ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      echo "Usage: $0 [--repull] [--restart-only]" >&2
      exit 1
      ;;
  esac
done

cd "${DEPLOY_DIR}"

if [[ "${REPULL}" == "true" ]]; then
  docker compose pull launch-lms migrate
fi

docker compose up -d db redis

if [[ "${RESTART_ONLY}" != "true" ]]; then
  docker compose run --rm migrate
fi

docker compose up -d launch-lms
"${SCRIPT_DIR}/verify-deploy.sh"
