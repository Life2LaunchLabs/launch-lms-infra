---
tracker:
  kind: jira
  provider:
    base_url: $JIRA_BASE_URL
    email: $JIRA_EMAIL
    api_token: $JIRA_API_TOKEN
    project_key: BOT
  required_labels: [symphony]
  active_states: ["To Do", "In Progress"]
  terminal_states: [Done]
polling:
  interval_ms: 30000
workspace:
  root: /home/node/workspaces
hooks:
  after_create: |
    git clone --branch dev --single-branch https://github.com/Life2LaunchLabs/launch-lms.git .
  timeout_ms: 180000
agent:
  max_concurrent_agents: 1
  max_turns: 30
  max_retry_backoff_ms: 300000
codex:
  command: codex --config shell_environment_policy.inherit=all --config 'forced_login_method="api"' --config 'model="gpt-6-astra"' --config 'model_reasoning_effort="medium"' app-server
  approval_policy: never
  thread_sandbox: danger-full-access
  turn_sandbox_policy:
    type: externalSandbox
    networkAccess: enabled
  turn_timeout_ms: 3600000
server:
  host: 0.0.0.0
  port: 8788
---
You are delivering {{ issue.identifier }}: {{ issue.title }} unattended on the dev server.
{{ issue.description }}
{% if attempt %}This is continuation/retry {{ attempt }}. Resume existing work and inspect the workpad and PR before repeating work.{% endif %}

The owner authorized eligible BOT parent Stories/Tasks to be implemented, checked,
merged through a PR into dev, and automatically deployed while they are away.
Read AGENTS.md and docs/agent-harness.md before editing. The September 2026 owner
pivot retires productOS as an execution dependency: use the native jira_rest tool
directly, never require a sibling productOS checkout or local SQLite/outbox sync.
If this checkout predates that harness, these workflow instructions apply immediately.

1. Fetch /rest/api/3/issue/{{ issue.identifier }} with fields summary,description,
   status,issuetype,subtasks,labels,issuelinks and read its paginated comments.
   Only work on non-subtask Story/Task issues with the symphony label. For an
   ineligible issue, stop without implementation. Never implement an Idea or FEED.
2. For To Do, fetch transitions and move the parent to In Progress. For In Progress,
   resume. If the owner removed symphony or moved it to In Review/Done, stop.
3. Find/create ONE Jira comment headed 'Symphony workpad', using Jira ADF. Keep it
   updated with plan, acceptance criteria, branch/PR, checks, blockers and evidence.
   Read linked deliverable subtasks and comments; they belong to the parent task.
4. Fetch origin dev; create symphony/<issue-key> from origin/dev for new work. On
   retry preserve uncommitted work, inspect existing PRs and reconcile before acting.
   Search existing/legacy/permission-gated implementations before changing behavior.
5. Implement all deliverables and run relevant checks. For UI changes use the design
   catalog and existing selected references; exercise real browser interactions at
   desktop/mobile and inspect screenshots. Missing references/browser/test data must
   become a precise blocker, never a fabricated visual pass.
6. Commit and push the delivery branch, create/update a PR targeting dev. Resolve
   actionable PR feedback. Wait for ALL six required checks on the CURRENT head:
   contract; api-lint / ruff; api-tests / test; migrations / alembic-heads;
   Build and smoke (amd64); Build and smoke (arm64). Also require any browser-ui
   check to succeed. Do not use --admin, bypass checks, or push directly to dev.
   If branch is behind, merge origin/dev, fix conflicts and wait for new checks.
7. Merge using gh pr merge --squash --match-head-commit <tested-sha>. This owner
   instruction authorizes the merge; do not wait for another approval. Keep the
   Jira parent In Progress through merge and deployment verification.
8. Follow the merged dev commit's 'Build Community Images' workflow and
   the infra 'Deploy environment' run triggered by its candidate dispatch. Inspect
   run conclusions and the deployed /api/v1/instance/build response on life2launch.dev.
   If the tester HTTP gate blocks the public endpoint, use the matched infra run
   verification log as deployed-commit evidence; never bypass the gate.
   Confirm the running commit equals the merge commit (or a later verified dev
   descendant). A merge alone is NOT proof of deployment. If deployment fails,
   record the failing run URL and exact blocker; never mark handoff complete.
9. When implementation/checks/deployment succeed, add concise owner test steps and
   evidence to the workpad; move finished deliverable subtasks and then parent to
   In Review using available Jira transitions. Never move anything to Done.
10. If blocked, update the workpad with exact missing input and remove only the
    symphony label (preserve other labels); leave parent In Progress. The owner
    re-adds symphony after resolving it. Do not spin endlessly or invent approval.

Keep production untouched. Do not access production hosts or credentials. No FEED
triage, replies, or status propagation. Never print credentials. Do not change branch
protection, deploy switches, or the running application directly. Runtime deployment
is owned by the existing checked-image GitHub Actions pipeline.

Browser support is provisioned: Bun and Playwright OS dependencies, isolated test-db
and test-redis, UI_TEST_DATABASE_URL/UI_TEST_REDIS_URL and synthetic login variables.
Install repository-pinned browser binaries as needed. Follow browser-ui.yaml to seed
ONLY this test database, and scripts/ui/run-local.sh (HTTP/dev mode) for captures.
The runner is constrained to 2300 MiB: do not build images locally. For a full build
or browser run that exceeds memory, run CI with screenshot retention and download
artifacts to inspect. Never declare a visual pass without inspecting screenshots.
Reading an explicitly linked FEED report/attachment as evidence is allowed; no FEED
writes, triage or propagation. Preserve sensitive reference images privately.
