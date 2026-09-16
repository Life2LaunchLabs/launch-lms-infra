# Dedicated control-plane host

Do not install this stack on an application host. Provision an Ubuntu 24.04 host
with an operations-only domain, encrypted provider backups, and a firewall allowing
80/443 publicly and SSH only from operator/Actions addresses. PostgreSQL, the API,
and the Symphony status network are never publicly published.

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
