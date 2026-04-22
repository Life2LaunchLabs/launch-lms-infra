#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

source "${SCRIPT_DIR}/load-release-env.sh"

cd "${DEPLOY_DIR}"

expected_image="${LAUNCHLMS_IMAGE}"
configured_launch_image="$(docker compose config --images launch-lms | tail -n 1)"
configured_migrate_image="$(docker compose config --images migrate | tail -n 1)"
launch_container_id="$(docker compose ps -q launch-lms)"
db_container_id="$(docker compose ps -q db)"
redis_container_id="$(docker compose ps -q redis)"

if [[ "${configured_launch_image}" != "${expected_image}" ]]; then
  echo "Configured launch-lms image does not match release lock." >&2
  echo "Expected: ${expected_image}" >&2
  echo "Actual:   ${configured_launch_image}" >&2
  exit 1
fi

if [[ "${configured_migrate_image}" != "${expected_image}" ]]; then
  echo "Configured migrate image does not match release lock." >&2
  echo "Expected: ${expected_image}" >&2
  echo "Actual:   ${configured_migrate_image}" >&2
  exit 1
fi

running_ref="$(docker inspect --format '{{.Config.Image}}' "${launch_container_id}")"
if [[ "${running_ref}" != "${expected_image}" ]]; then
  echo "Running launch-lms image does not match release lock." >&2
  echo "Expected: ${expected_image}" >&2
  echo "Actual:   ${running_ref}" >&2
  exit 1
fi

build_info="$(docker compose exec -T launch-lms sh -lc 'cat /app/build-info.json')"
build_version="$(printf '%s' "${build_info}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
build_commit="$(printf '%s' "${build_info}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit_sha"])')"
build_alembic_head="$(printf '%s' "${build_info}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["alembic_head"])')"

if [[ "${build_commit}" != "${LAUNCHLMS_RELEASE_COMMIT_SHA}" ]]; then
  echo "Running app commit does not match release lock." >&2
  echo "Expected: ${LAUNCHLMS_RELEASE_COMMIT_SHA}" >&2
  echo "Actual:   ${build_commit}" >&2
  exit 1
fi

current_revision="$(docker compose run --rm --entrypoint sh migrate -lc 'cd /app/api && uv run alembic current' | tail -n 1 | awk '{print $1}')"
if [[ "${current_revision}" != "${build_alembic_head}" ]]; then
  echo "Database revision does not match the image Alembic head." >&2
  echo "Expected: ${build_alembic_head}" >&2
  echo "Actual:   ${current_revision}" >&2
  exit 1
fi

db_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "${db_container_id}")"
redis_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "${redis_container_id}")"

echo "Release version: ${LAUNCHLMS_RELEASE_VERSION}"
echo "Release commit:  ${LAUNCHLMS_RELEASE_COMMIT_SHA}"
echo "Running image:   ${running_ref}"
echo "App version:     ${build_version}"
echo "Alembic head:    ${build_alembic_head}"
echo "Database health: ${db_health}"
echo "Redis health:    ${redis_health}"
echo "Deploy verification passed."
