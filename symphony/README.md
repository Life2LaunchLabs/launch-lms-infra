# Symphony on the dev server

Pinned OpenAI Symphony v0.0.2 with Codex 0.154.0 runs one agent in a separate,
resource-limited Compose project. It cannot mount the Docker socket, application
volumes, host checkout, or production credentials. Its dashboard binds host loopback
only. Persistent workspaces, Codex login refreshes and logs live in symphony-home.

## Install

On the unstable x86_64 host, provision /etc/launch-symphony (0700) containing:
- runner.env (0600): JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN and GH_TOKEN.
- auth.json (0600, uid 1000): the operator's authorized Codex login bootstrap.
- ENABLED: explicit opt-in marker. Never enable this on production.

The GitHub identity needs app contents/PR write and Actions read on app + infra;
it merges through existing rules and never bypasses checks. Credentials are never
stored in Git. The bootstrap login is copied only once: later token refreshes persist
in the volume. To deliberately replace login, stop the runner and replace its private
volume auth.json from a newly authorized login before restarting.

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

On the 4 GiB dev droplet the worker is limited to 2300 MiB, 1.5 CPU and 512 PIDs;
large image/browser checks run in GitHub Actions. Inspect OOM state when a worker
disappears. Expand server capacity before increasing concurrency.

Upstream contract: https://github.com/openai/symphony/blob/v0.0.2/SPEC.md
Adapter + label filter verified in v0.0.2 source. Further harness docs live in the
application's AGENTS.md and docs/agent-harness.md. productOS is archived context,
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

Codex uses its supported legacy Landlock sandbox inside Docker because the host
disallows nested unprivileged user namespaces required by bubblewrap. Workspace
write restrictions and approval_policy=never remain active; Docker privileges and
namespace restrictions are not relaxed. Revalidate this flag on CLI upgrades.
