# Dedicated control-plane host

Do not install this stack on an application host. Provision an Ubuntu 24.04 host
with an operations-only domain, encrypted provider backups, and a firewall allowing
80/443 publicly and SSH only from operator/Actions addresses. PostgreSQL, the API,
and the Symphony status network are never publicly published.

## Repository-managed host

Cloud resources live under `deploy/control-plane/iac`; host configuration lives
under `deploy/control-plane/ansible`. Do not bootstrap the server with an ad-hoc
shell session. The protected `Control-plane host` workflow validates both on pull
requests and exposes four explicit operations: `plan`, `apply`, `configure`, and
`verify`. Cloud and host mutations require approval through the `operations`
GitHub Environment.

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
by dispatching `apply` with droplet ID `601077988`; `prevent_destroy` blocks an
accidental replacement. Review the plan before approving apply. Thereafter the
same workflow owns drift correction. DigitalOcean does not permit changing a
Droplet's creation-time SSH keys in place, so an SSH-key mismatch must never be
accepted as an unreviewed replacement.

## Repository-managed service deployment

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
