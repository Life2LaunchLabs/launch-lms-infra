# Symphony on the dev server

Pinned OpenAI Symphony v0.0.2 with Codex 0.154.0 runs one agent in a separate,
resource-limited Compose project. It cannot mount the Docker socket, application
volumes, host checkout, or production credentials. Its dashboard binds host loopback
only. Persistent workspaces, API authentication and logs live in symphony-home.

## Install

On the dedicated operations host, provision /etc/launch-symphony (0700) containing:
- runner.env (0600): JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN,
  OPERATIONS_RUNNER_BROKER_KEY and OPERATIONS_PROJECT_ID. GitHub access is minted
  at startup by the control plane; do not store a long-lived GH_TOKEN.
- compose.env (0600): `SYMPHONY_MEMORY_LIMIT=4g`. The dedicated host must have at
  least 8 GiB RAM; the 2300 MiB Compose default exists only for the legacy host.
- openai-api-key (0600, uid 1000): plain API key for the funded OpenAI API project.
- ENABLED: explicit opt-in marker. Never enable this on production.

The GitHub identity needs app contents/PR write and Actions read on app + infra;
it merges through existing rules and never bypasses checks. Credentials are never
stored in Git. Startup requires the API key file and logs in using stdin, replacing
any previous ChatGPT login. App-server forces API authentication; there is no account
usage fallback. The key is mounted read-only and never included in process arguments,
Compose environment inspection, Git or logs. Codex also stores its API auth privately
in the persistent home volume. Use a dedicated project key for this worker.

To rotate: replace the host key file, preserve uid 1000/mode 0600, then recreate the
worker with `bash scripts/deploy-symphony.sh`. Keep dispatch paused until a minimal
API request succeeds. API charges and rate limits belong to the funded project; one
worker and 30 turns are concurrency/turn bounds, not a dollar budget. No fast/priority
service override is configured. Model access must be verified with the supplied key.

Run `bash scripts/deploy-symphony.sh` from the checked-out infra release. Normal
unstable app deployments also apply this service when ENABLED exists. App merges
into dev already publish a checked candidate and dispatch infra deployment; the
worker records the deployed commit before moving a task to In Review.

## Board contract

Existing statuses stay: Idea, To Do, In Progress, In Review, Done. Add `symphony`
only to formalized parent Story/Task issues ready for autonomous delivery, never
subtasks. This opt-in label is the new execution taxonomy. The initial canary is
BOT-217. An In Progress label means this runner owns/resumes that task, so do not
apply it to another worker's active task. Done remains owner signoff.

Blocked workers remove the label and leave an exact blocker in their workpad.
Resolve the blocker and re-add the label to resume. In Review tasks are not polled.
FEED intake remains app -> Jira; productOS triage/status propagation is paused.

## Operations

`docker compose -f docker-compose.symphony.yml ps`
`docker compose -f docker-compose.symphony.yml logs --tail 100`
`curl -fsS http://127.0.0.1:8788/api/v1/state`

Dashboard tunnel: `ssh -L 8788:127.0.0.1:8788 root@137.184.34.50`, then open
http://127.0.0.1:8788. Do not expose the unauthenticated dashboard publicly.

Pause durably: `docker compose -f docker-compose.symphony.yml exec symphony touch /home/node/PAUSED`,
then `docker compose -f docker-compose.symphony.yml restart symphony`.
Resume: remove /home/node/PAUSED in the container, then restart. Pause stops the
current agent; Git/workpad state remains for continuation. To disable all future
starts, remove the host ENABLED marker AND run Compose stop. App deployments leave
this separate project alone. Do not delete its volume during routine updates.

The legacy worker is limited to 2300 MiB; the dedicated host uses a 4 GiB worker
limit. Both retain 1.5 CPU and 512 PIDs. Large image/browser checks run in GitHub
Actions. The September 13 incident proved that a local Next server can exhaust the
legacy cgroup; see `docs/operations/symphony-oom-2026-09-13.md`.
Use `python3 scripts/symphony-state.py inspect` for the redacted resource record and
`python3 scripts/symphony-state.py preflight` for the destination acceptance gate.
Do not resume dispatch until preflight passes after the synthetic browser canary.
Host transfer, verified snapshot/restore, continuation, and rollback are defined in
`deploy/control-plane/SYMPHONY_MIGRATION.md`.

Upstream contract: https://github.com/openai/symphony/blob/v0.0.2/SPEC.md
Adapter + label filter verified in v0.0.2 source. Further harness policy lives in the
application's AGENTS.md and WORKFLOW.md. productOS is archived context,
not a required sibling checkout or parallel Jira writer.

## Browser fixtures

Dedicated test-db (pgvector) and test-redis services have no published ports and no
application data. The worker receives UI_TEST_DATABASE_URL/UI_TEST_REDIS_URL plus
synthetic UI_TEST_EMAIL/PASSWORD. Bun and browser OS dependencies are preinstalled;
install the repository-pinned browsers with `bunx playwright install chromium webkit`
after `bun install --frozen-lockfile`. Use `scripts/ui/run-local.sh` with HTTP and
local dev mode, migrate/seed only this disposable database first (see browser-ui.yaml).
Never point this harness at the shared running app database. Captures stay private
in the persistent task workspace. If memory prevents full-stack local verification,
use the repository browser CI and retain/download screenshots for visual inspection.

Docker is the external sandbox boundary. Codex uses externalSandbox with network
access enabled inside the non-root, capability-dropped container. Nested bubblewrap
is unavailable on this host; legacy Landlock is incompatible with writable profiles
in this CLI. The agent can write its entire persistent home volume, not only one
task checkout. Concurrency stays one; no live app files, Docker socket, production
credentials or app database volumes are mounted. The thread API uses full access
inside this container, never on the host.

API-key mode supersedes the initial ChatGPT device-login setup.

## Owner review and Merge gate (supersedes earlier automatic-merge notes)

To Do + symphony -> In Progress -> In Review with Jira attachments -> owner moves
parent to Merge -> agent verifies the reviewed head, merges and watches deployment.
After successful deployment it remains in Merge, removes symphony and records
“Deployed — ready for owner signoff.” Owner moves it to Done. For rework, comment
with feedback and move In Review back to To Do. No approval comment is necessary.

The Merge column must map to a status named exactly merge (lowercase) for parent Tasks/Stories.
Agents never move a task into Merge. A changed PR head requires fresh evidence and
another owner move; branch protections and current-head checks still apply.

Evidence uploader runs beside Symphony without model usage. It accepts each task's
.symphony-review-request.json, attaches synthetic screenshots/video/report directly
to Jira, writes launch-symphony-review with exact SHA, then transitions In Review.
Jira token stays in the uploader/orchestrator environment, not Codex's child env.
Upload failures keep the task from completing handoff. See WORKFLOW.md for manifest.
The uploader reads only files inside that task workspace, rejects traversal/symlink
escapes and oversized/unapproved file formats, and verifies current required checks.
