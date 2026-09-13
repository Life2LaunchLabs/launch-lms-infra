#!/usr/bin/env bash
set -euo pipefail
umask 077
mkdir -p "$HOME/.codex" "$HOME/workspaces" "$HOME/logs"
if [[ ! -s "$HOME/.codex/auth.json" ]]; then
  cp /run/secrets/codex-auth.json "$HOME/.codex/auth.json"
fi
: "${GH_TOKEN:?GitHub token required}"
: "${JIRA_BASE_URL:?Jira URL required}"
: "${JIRA_EMAIL:?Jira email required}"
: "${JIRA_API_TOKEN:?Jira token required}"
git config --global user.name "Launch LMS Symphony"
git config --global user.email "symphony@life2launch.dev"
gh auth setup-git
codex -c 'service_tier="fast"' login status
cp /opt/symphony/WORKFLOW.md "$HOME/WORKFLOW.md"
# Pause persists across app deployments and worker rebuilds.
if [[ -f "$HOME/PAUSED" ]]; then
  sed -i 's/active_states: \["To Do", "In Progress"\]/active_states: []/' "$HOME/WORKFLOW.md"
fi
exec symphony --i-understand-that-this-will-be-running-without-the-usual-guardrails --logs-root "$HOME/logs" "$HOME/WORKFLOW.md"
