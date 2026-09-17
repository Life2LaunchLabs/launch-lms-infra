#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/load-release-env.sh
test "$DEPLOY_ENVIRONMENT" = unstable
python3 - "$RELEASE_LOCK" .deploy-state/deployed-release.json <<'PY'
import json
import sys
from pathlib import Path
accepted = json.loads(Path(sys.argv[1]).read_text())
deployed = json.loads(Path(sys.argv[2]).read_text())
if any(accepted.get(key) != deployed.get(key) for key in ('commit_sha', 'image_digest')):
    raise SystemExit('Accepted lock differs from the verified deployed application image')
PY
test "${FEEDBACK_MIGRATION_MODE:-}" = audit || test "$FEEDBACK_MIGRATION_MODE" = diagnose || test "$FEEDBACK_MIGRATION_MODE" = apply
for name in LAUNCHLMS_OPERATIONS_SUBJECT_SECRET LAUNCHLMS_FEEDBACK_JIRA_BASE_URL LAUNCHLMS_FEEDBACK_JIRA_EMAIL LAUNCHLMS_FEEDBACK_JIRA_API_TOKEN; do
  test -n "${!name:-}" || { echo "Missing protected migration secret: $name" >&2; exit 1; }
done
test "${LAUNCHLMS_FEEDBACK_JIRA_PROJECT_KEY:-}" = FEED
docker compose exec -T db pg_isready -U launchlms >/dev/null
export COMPOSE_FILE="$COMPOSE_FILE:$PWD/deploy/feedback-migration/compose.yml"
if [[ "$FEEDBACK_MIGRATION_MODE" == diagnose ]]; then
  docker compose run --rm --no-deps -T \
    -e LAUNCHLMS_FEEDBACK_JIRA_BASE_URL \
    -e LAUNCHLMS_FEEDBACK_JIRA_EMAIL \
    -e LAUNCHLMS_FEEDBACK_JIRA_API_TOKEN \
    -e LAUNCHLMS_FEEDBACK_JIRA_PROJECT_KEY \
    -v "$PWD/deploy/feedback-migration/diagnose.py:/tmp/feedback-diagnose.py:ro" \
    --entrypoint sh migrate -lc 'cd /app/api && uv run python /tmp/feedback-diagnose.py'
  exit 0
fi
if [[ "$FEEDBACK_MIGRATION_MODE" == apply ]]; then
  [[ "${FEEDBACK_MIGRATION_EXPECTED_COUNT:-}" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid approved issue count' >&2; exit 1; }
  [[ "${FEEDBACK_MIGRATION_EXPECTED_DIGEST:-}" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid approved audit digest' >&2; exit 1; }
  script_arguments=(--apply --expected-count "$FEEDBACK_MIGRATION_EXPECTED_COUNT" --expected-digest "$FEEDBACK_MIGRATION_EXPECTED_DIGEST")
else
  script_arguments=()
fi
docker compose run --rm --no-deps -T \
  -e LAUNCHLMS_OPERATIONS_SUBJECT_SECRET \
  -e LAUNCHLMS_FEEDBACK_JIRA_BASE_URL \
  -e LAUNCHLMS_FEEDBACK_JIRA_EMAIL \
  -e LAUNCHLMS_FEEDBACK_JIRA_API_TOKEN \
  -e LAUNCHLMS_FEEDBACK_JIRA_PROJECT_KEY \
  --entrypoint sh migrate -lc 'cd /app/api && test -f scripts/migrate_feedback_ownership.py && uv run python scripts/migrate_feedback_ownership.py "$@"' \
  migration "${script_arguments[@]}"
