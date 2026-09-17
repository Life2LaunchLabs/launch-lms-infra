#!/usr/bin/env bash
set -euo pipefail
umask 077
mkdir -p "$HOME/.codex" "$HOME/workspaces" "$HOME/logs"
# Require API billing on every start; replace stale ChatGPT credentials privately.
api_key_file="${SYMPHONY_API_KEY_FILE:-/run/secrets/openai-api-key}"
if [[ ! -s "$api_key_file" ]]; then
  echo 'Missing worker API key: provision /etc/launch-symphony/openai-api-key' >&2
  exit 1
fi
codex -c 'forced_login_method="api"' login --with-api-key < "$api_key_file" >/dev/null 2>&1 || {
  echo 'Worker API-key login failed; dispatch remains stopped' >&2
  exit 1
}
: "${GH_TOKEN:?GitHub token required}"
: "${JIRA_BASE_URL:?Jira URL required}"
: "${JIRA_EMAIL:?Jira email required}"
: "${JIRA_API_TOKEN:?Jira token required}"
git config --global user.name "Launch LMS Symphony"
git config --global user.email "symphony@life2launch.dev"
gh auth setup-git
echo 'Codex authentication: API key (API project billing)'
project_id="${OPERATIONS_PROJECT_ID:-launch-lms}"
project_repo="$(PYTHONPATH=/opt/platform python3 -c "from pathlib import Path; from services.orchestrator.manifest import load_project; print(load_project('$project_id', Path('/opt/platform')).repository)")"
project_branch="$(PYTHONPATH=/opt/platform python3 -c "from pathlib import Path; from services.orchestrator.manifest import load_project; print(load_project('$project_id', Path('/opt/platform')).data['source']['delivery_branch'])")"
product_commit="$(gh api "repos/$project_repo/commits/$project_branch" --jq .sha)"
render_args=()
if [[ -f "$HOME/PAUSED" ]]; then
  render_args+=(--paused)
fi
PYTHONPATH=/opt/platform python3 -m services.orchestrator.render_workflow \
  "$project_id" "$product_commit" --output "$HOME/WORKFLOW.md" --metadata "$HOME/rendered-workflow.json" "${render_args[@]}"
echo "Rendered product workflow at $product_commit"
exec python3 /opt/symphony/supervise.py
