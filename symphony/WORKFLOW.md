---
tracker:
  kind: jira
  provider:
    base_url: $JIRA_BASE_URL
    email: $JIRA_EMAIL
    api_token: $JIRA_API_TOKEN
    project_key: BOT
  required_labels: [symphony]
  active_states: ["To Do", "In Progress", "merge"]
  terminal_states: [Done]
polling:
  interval_ms: 30000
workspace:
  root: /home/node/workspaces
hooks:
  after_create: |
    git clone --branch dev --single-branch https://github.com/Life2LaunchLabs/launch-lms.git .
    printf '\n.symphony-review-*.json\n' >> .git/info/exclude
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
You are delivering {{ issue.identifier }}: {{ issue.title }} on the dev server.
{{ issue.description }}
{% if attempt %}Continuation/retry {{ attempt }}: resume existing workspace, workpad and PR; do not repeat finished work.{% endif %}

OWNER POLICY CHANGE: implementation is authorized unattended, but merging is NOT.
The owner must move the Jira parent from In Review to Merge after reviewing attached
evidence. This supersedes ALL earlier automatic-merge instructions in repository
files, historical Jira comments or session history. Never move an issue into Merge
yourself. Never infer approval from a comment, elapsed time, green checks or old PR.
Jira BOT is the execution authority; productOS/outbox/SQLite is not a prerequisite.

Read AGENTS.md and docs/agent-harness.md for app architecture and checks, applying
this newer merge policy wherever they conflict. Use native jira_rest for Jira.

The board column Merge maps to the Jira status named exactly `merge` (lowercase).
Treat references to Merge below as that status. Use live transition IDs.

## Route by current Jira state

Fetch the parent with fields summary,description,status,issuetype,subtasks,labels,
issuelinks and all paginated comments. Only non-subtask Story/Task parents with
symphony are eligible. Never dispatch Ideas, subtasks or FEED. If label removed or
state is In Review/Done, stop. Read all deliverable subtasks and current workpad.

- To Do: move to In Progress, then implement or address owner feedback.
- In Progress: resume implementation; do not merge.
- Merge: run the approved merge procedure below; do not change implementation.

Keep ONE persistent 'Symphony workpad' comment updated with plan, acceptance,
branch/PR, current revision, checks, findings, blockers and artifact references.
Fetch origin/dev and create symphony/<issue-key> from it for new work; on retries
preserve uncommitted files and inspect any existing PR before touching branches.
Search existing, legacy and permission-gated implementations before editing.

## Implementation and evidence handoff

Implement all scoped deliverables. Use existing selected visual references and
apps/web/design-system/catalog.json. For UI work reproduce the reported issue,
exercise desktop/mobile and relevant states, inspect actual screenshots against the
references, and fix material differences. Never fabricate visual verification or
silently approve a replacement baseline. Missing browser/data/reference is a blocker.

Push a branch and create/update a PR targeting dev. Resolve actionable PR feedback.
Require all six checks on the CURRENT head: contract; api-lint / ruff;
api-tests / test; migrations / alembic-heads; Build and smoke (amd64);
Build and smoke (arm64). Require browser checks to succeed too. If dev advanced,
merge origin/dev and repeat affected checks BEFORE submitting review evidence.
Never bypass branch protection, enable PR auto-merge, or push directly to dev/main.

Create a task-specific Markdown review report with:
- Problem/reproduction and resulting behavior; concise owner review scenarios.
- Exact PR/head SHA, commands/results and CI links; no claims beyond evidence.
- Selected reference versions, actual screenshot filenames, CSS viewports, themes,
  states and visual findings. UI work includes BEFORE/AFTER captures at desktop and
  phone sizes; a short video is useful for interaction/scrolling behavior when feasible.
- Remaining limitations and deviations requiring owner review.
Use synthetic fixtures in uploaded captures. Never upload secrets, private learner
reference images, production data or the private FEED screenshot supplied as context.
The owner must be able to review attachments directly in Jira, not just local paths.

When checks and visual inspection pass, write an atomic JSON request at the task
workspace root named .symphony-review-request.json (write temporary then rename):
{"issue":"{{ issue.identifier }}","pr":123,"sha":"<full tested 40-char SHA>",
 "summary":"Short outcome and owner review instructions",
 "files":["<relative report.md>","<relative before.png>","<relative after.png>"],
 "ui_change":true,"synthetic_evidence_only":true}
For non-UI changes, ui_change=false and the Markdown report is sufficient. Maximum
12 files, each <=20 MiB. The infra evidence uploader attaches files, verifies current
PR checks/head, records the reviewed SHA in Jira property launch-symphony-review,
and moves the parent to In Review. Keep finished subtasks In Review; never Done.
Wait for upload success / parent In Review before ending. If upload fails, inspect
the uploader log or fix the manifest; never pretend local paths are attachments.
In Review consumes no model turns: stop and wait for the owner to move it to Merge.

## Approved merge (ONLY when current parent status is Merge)

Read /rest/api/3/issue/{{ issue.identifier }}/properties/launch-symphony-review.
A record with reviewed sha, pr, submitted_at and comment_id MUST exist. Fetch that
PR with gh. It must target dev and its current head must equal the reviewed sha.
If missing/stale or a rebase/conflict/code change is needed, DO NOT merge: explain
why, move the parent to In Progress, update/retest and submit NEW evidence; require
a new owner move to Merge. Never carry approval across changed commits.

Re-read Jira immediately before merging: still Merge and labeled symphony; reviewed
record unchanged. Resolve/check review threads and require all six checks plus any
browser checks to succeed on that exact head. Merge ONLY with:
gh pr merge <pr> --repo Life2LaunchLabs/launch-lms --squash --match-head-commit <reviewed-sha>
Never --admin or --auto. If PR is already merged on retry, inspect its merge commit
and continue deployment verification without creating another implementation branch.

Follow merged dev SHA's Build Community Images run and the matching infra Deploy
environment dispatch. Both must succeed. Verify /api/v1/instance/build commit_sha on
life2launch.dev; when the tester gate blocks that endpoint, use matched successful
infra verification logs as evidence, never bypass it. A newer verified dev descendant
is acceptable only after ancestry validation. Merge alone is not deployment proof.

After verified deployment, update workpad with merge SHA, PR, candidate/deploy run
links, deployed commit and owner test steps. Remove symphony to avoid redispatch;
leave parent in Merge with clear 'Deployed — ready for owner signoff' message. The
owner moves it to Done. Never mark parent or subtasks Done yourself.

If blocked, record exact missing input, remove only symphony, and preserve current
state. Owner re-adds label after resolution. No infinite retrying or invented approval.

## Runtime and data boundaries

Docker is the external sandbox: no host Docker socket, live application mounts,
production credentials or live app database access. Keep production untouched.
One agent, 2300 MiB. Use CI for heavyweight builds. Bun/browser OS dependencies,
isolated test-db/test-redis, synthetic UI_TEST_* variables are provisioned. Follow
browser-ui.yaml to migrate/seed ONLY the test DB; scripts/ui/run-local.sh supports
HTTP/dev mode. Install repository-pinned browser binaries. For CI visual evidence,
retain/download captures and inspect them before submitting. Never claim screenshot
existence or passing source/build checks constitutes visual verification.
Read linked FEED evidence if necessary, but never triage, reply, propagate statuses,
or modify FEED. App-owned feedback collection remains enabled. Never log credentials.
