# Dedicated control-plane host

Owner account setup: [OWNER_SETUP.md](OWNER_SETUP.md). This is preparation, not
authorization to move the operations apex or deploy the control plane.

Do not install this stack on an application host. Provision an Ubuntu 24.04 host
with an operations-only domain, encrypted provider backups, and a firewall allowing
80/443 publicly and SSH only from operator/Actions addresses. PostgreSQL, the API,
and the Symphony status network are never publicly published.

## Repository-managed host

Cloud resources live under `deploy/control-plane/iac`; host configuration lives
under `deploy/control-plane/ansible`. Do not bootstrap the server with an ad-hoc
shell session. The protected `Control-plane host` workflow validates both on pull
requests and exposes six explicit operations: `prepare-state`, `plan`,
`adopt-plan`, `apply`, `configure`, and `verify`. Cloud and host mutations
require approval through the `operations` GitHub Environment.

The accepted initial host is `launch-operations-1` in SFO3: Ubuntu 24.04 x86_64,
8 GiB RAM, four vCPUs, provider backups and monitoring. OpenTofu manages the
Droplet, project, and Cloud Firewall. Ansible manages packages, SSH hardening,
UFW, fail2ban, unattended upgrades, bounded Docker logs, 4 GiB emergency swap,
protected directories, and an exact-revision checkout. It does not create secrets
or start the control plane or Symphony.

OpenTofu uses a private versioned DigitalOcean Spaces bucket with S3 lock files.
Configure the `operations` environment with:

- secrets `DIGITALOCEAN_TOKEN`, `SPACES_ACCESS_KEY_ID`,
  `SPACES_SECRET_ACCESS_KEY`, `OPERATIONS_SSH_PRIVATE_KEY`, and
  `OPERATIONS_SSH_HOST_KEY`;
- variables `OPERATIONS_STATE_BUCKET`,
  `OPERATIONS_SSH_KEY_NAMES_JSON`, and
  `OPERATIONS_SSH_SOURCE_CIDRS_JSON`.

The SSH private key is a dedicated operations-Actions identity, not an operator's
personal key. Its public half is installed idempotently alongside operator access.
The SSH host-key secret is the complete pinned known-hosts line, not a keyscan
performed during deployment. The initial manually-created droplet is adopted once
by dispatching `adopt-plan` with droplet ID `601077988`; this records it in remote
state and produces a post-adoption plan without applying cloud changes.
`prevent_destroy` blocks an accidental replacement. Review that plan before
dispatching `apply`. Thereafter the same workflow owns drift correction.
DigitalOcean does not permit changing a Droplet's creation-time SSH keys in
place and does not return their IDs after import. OpenTofu therefore ignores
post-creation `ssh_keys` drift while retaining the configured keys for host
creation; Ansible owns ongoing administrative-key installation and rotation.
The host is organized through its dedicated DigitalOcean project rather than
optional provider tags, keeping the automation token's permissions narrower.

## Repository-managed service deployment

The protected private stage procedure is documented in
[PRIVATE_STAGE.md](PRIVATE_STAGE.md). Run it before the public apex cutover.

The protected `Deploy control plane` workflow transfers two complete environment
files from `operations` Environment secrets, installs them mode `0600`, checks out
the exact protected-branch revision as `launchops`, and runs
`scripts/deploy-control-plane.sh` under a host deployment lock. Configure:

- `OPERATIONS_CONTROL_PLANE_ENV` from `control-plane.env.example`, including
  `OPERATIONS_EXPECTED_IP=143.110.225.231`;
- `OPERATIONS_POSTGRES_ENV` from `postgres.env.example`, using the same URL-safe
  PostgreSQL password as the database URL.

Deployment refuses placeholders, short session secrets, mismatched database
credentials, non-production mode, non-HTTPS public URLs, and DNS that does not
resolve the configured domain to the expected host. Only then does it build/start
Compose and verify public HTTPS. The workflow is manual until backup/restore and
deployment observation have completed their first live acceptance cycle.

Symphony can be staged before public control-plane DNS. The protected
`Deploy Symphony on operations host` workflow uses the pinned runner image,
adds an operations-host resource override, and starts a first-time worker
paused with its status port on loopback. Supply the `operations` Environment
secrets `OPERATIONS_SYMPHONY_RUNNER_ENV` and
`OPERATIONS_SYMPHONY_OPENAI_API_KEY`; see [the worker runbook](../../symphony/README.md)
for the required environment file and cutover gate. The protected
`Cut over Symphony to operations host` workflow preserves and disables the
legacy worker before resuming the staged one. Neither workflow changes DNS,
starts the public portal, or grants production deployment credentials.

Public origins and the staged DNS cutover are defined in
`deploy/environments/launch-lms.yaml`. The deployment refuses to start while its
`operations_apex_cutover` gate is false. Follow `deploy/environments/README.md`;
do not temporarily publish the control plane on a second hostname.

## Required host configuration

Create `/etc/launch-operations` mode `0700` with:

- `postgres.env` mode `0600`: `POSTGRES_USER=operations`, `POSTGRES_DB=operations`,
  and a unique `POSTGRES_PASSWORD`.
- `control-plane.env` mode `0600`: database URL, operations public URL/domain,
  32+ byte session secret, GitHub OAuth client ID/secret, allowed organization and
  repository, and the internal Symphony status URL.
- distinct delivery Jira, feedback Jira, GitHub App, and worker OpenAI credentials
  provided only to the service that needs each capability.

Then run `docker compose -f deploy/control-plane/compose.yaml up -d --build --wait`.
Create the GitHub OAuth application with only the control-plane callback URL. There
is no public registration path.

## Backup and restore gate

Back up the PostgreSQL volume daily with encrypted off-host retention and snapshot
the persistent runner workspace independently. A backup is not accepted until a
scheduled restore into a disconnected test project passes schema inspection and a
sample run/evidence query. Record backup revision, encryption key version, restore
duration, row counts, and test result without recording secrets.

Before moving Symphony, pause dispatch, record current container/image/workspace
state, take a volume backup, restore on this host, and confirm unfinished workspace
continuation without redispatching completed work. Keep concurrency at one. The
runner receives no Docker socket, application database, live volumes, production
credentials, or deployment SSH key.
