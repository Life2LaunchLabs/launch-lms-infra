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
cp /opt/symphony/WORKFLOW.md "$HOME/WORKFLOW.md"
# Pause persists across app deployments and worker rebuilds.
if [[ -f "$HOME/PAUSED" ]]; then
  sed -i 's/^  active_states:.*/  active_states: []/' "$HOME/WORKFLOW.md"
fi
exec python3 /opt/symphony/supervise.py
