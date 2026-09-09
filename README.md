# Launch LMS deployments

Two independent installations use this repository's `main` branch:

| Target | Application source | Release selection | Data |
| --- | --- | --- | --- |
| Production | Verified `main` commit with a stable version tag | Tracked `release.lock.json`, merged through a PR | Real data |
| Unstable | Verified `dev` commit | `.deploy-state/unstable.lock.json`, supplied by the app workflow | Independent copy; operator refresh replaces test changes |

**Both run `LAUNCHLMS_ENV=prod` and `LAUNCHLMS_DEVELOPMENT_MODE=false`.** A new
unstable droplet is not a development server. Choose its environment at setup;
never switch an existing production installation to unstable in place.

Caddy terminates TLS and proxies to the app container at `launch-lms:80`. The app
container runs Next.js, FastAPI, Hocuspocus, and internal Nginx. PostgreSQL/pgvector,
Redis, and Ollama (`all-minilm:33m`) have persistent local volumes. Only Caddy
publishes host ports. The unstable override prevents the app, migrations, database,
and Redis from reaching the public network; Caddy and Ollama retain egress for
TLS and model downloads. Copied payment/SSO settings are cleared on refresh.

Read the [app deployment guide](https://github.com/Life2LaunchLabs/launch-lms/blob/main/scripts/docs/deployment.md)
for the feature branch → dev → unstable testing → main → version → infra PR flow.
That guide also contains the click-by-click GitHub token, environment, package,
SSH-key, branch-protection, and deployment-switch setup. The sections below are
the host-side runbook and can be followed independently from a fresh terminal.

## First rollout of this pipeline

Do these in order; leave automation disabled while preparing hosts.

1. Land the app and infra pipeline changes on their respective branches/default
   branch. Create the infra GitHub environments `production` and `unstable`.
   Leave their `DEPLOY_ENABLED` variable unset/false. No SSH deployment occurs
   until that environment's variable is `true`.
2. In the app repo add `INFRA_REPO_TOKEN` with infra Contents read/write and Pull
   requests read/write. Grant this infra repository GHCR package read access via
   the app package's **Manage Actions access** setting.
3. Build `dev` successfully to get a `candidate` artifact for unstable. Keep app
   variable `UNSTABLE_DEPLOY_ENABLED` false until unstable is bootstrapped.
4. Merge the approved app changes to `main`, wait for its successful candidate,
   and push a new stable `vMAJOR.MINOR.PATCH` tag. The release workflow retags the
   checked digest and opens a production infra lock PR. Merge that PR while
   production automation is still disabled. The original legacy tag-only lock
   is deliberately rejected by the new deploy scripts; do not guess a digest.
5. Initialize/migrate the hosts using the walkthroughs below. Then enable each
   environment's `DEPLOY_ENABLED=true` and app `UNSTABLE_DEPLOY_ENABLED=true`.
6. Configure required checks on app branches and infra PRs. Reviewers may be
   required for GitHub's production environment; unstable should be automatic.

## Initialize a fresh droplet

Create one droplet per environment. Use Ubuntu 24.04, persistent storage sized
for the DB/content plus at least one refresh copy, and enough RAM for the app and
CPU embeddings. Start by measuring the existing production workload; this guide
has not benchmarked a fixed droplet size. Both amd64 and arm64 app images are built.

1. In DigitalOcean, create an Ubuntu 24.04 droplet in the desired region. Add
   your normal workstation SSH key during creation so you retain an independent
   administration path. Record its public IPv4 address. In the cloud firewall,
   allow TCP 80 and 443 from everywhere, UDP 443 from everywhere for HTTP/3,
   and TCP 22 only from trusted administration/deployment sources. Do not expose
   PostgreSQL, Redis, Ollama, or application container ports publicly.
2. Point the environment domain's nameservers to DigitalOcean DNS. Create A
   records for `@` and `*` pointing to this droplet (and AAAA only if IPv6 is
   configured). Use a separate registrable domain for unstable. The current
   Caddy plugin is for DigitalOcean; other DNS providers need the matching plugin.
3. Connect using the key selected during droplet creation:

   ```bash
   ssh -i ~/.ssh/YOUR_ADMIN_KEY -o IdentitiesOnly=yes root@DROPLET_IP
   ```

   Install prerequisites and clone the repo. Run as root, or use a dedicated
   operator with Docker access and ownership of `/opt/launch-lms`:
   ```bash
   apt-get update
   apt-get install -y ca-certificates curl git python3
   curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
   # Inspect the installer before executing it.
   sh /tmp/get-docker.sh
   docker compose version
   git clone https://github.com/Life2LaunchLabs/launch-lms-infra /opt/launch-lms
   cd /opt/launch-lms
   ```
   For a private infra repo, configure a persistent read-only Git deploy key and
   clone with its SSH URL. Future deploys must be able to `git fetch origin main`
   without an interactive prompt. Clone into exactly `/opt/launch-lms`; the
   default content volume name and SSH workflow use that installation path.
4. In the DigitalOcean control panel, open **API → Tokens/Keys → Generate New
   Token**. Create a token that can manage DNS records for the account containing
   this environment's zone. Copy it immediately; setup stores it only in the
   root-readable host `.env`, and Caddy needs it later for wildcard certificate
   renewals. This is not a Caddy-specific token and should not be added to GitHub.
   Also prepare a unique initial administrator email/password. Do **not** copy
   production's `.env`, JWT key, database password, or admin password to unstable.
5. Authenticate Docker to GHCR with a short-lived classic PAT carrying
   `read:packages`, or another token authorized to read the app package. Let
   `docker login` prompt for the token; do not put it in shell history:
   ```bash
   docker login ghcr.io -u YOUR_GITHUB_USER
   ```
6. For **production**, the checked-out `release.lock.json` must contain the
   verified stable release digest from the rollout above:
   ```bash
   bash setup.sh production
   ```
   For **unstable**, download `candidate.json` from a successful app `dev` build
   on your workstation, then transfer it:
   ```bash
   gh run download DEV_BUILD_RUN_ID --repo Life2LaunchLabs/launch-lms --name candidate --dir /tmp/unstable-candidate
   scp -i ~/.ssh/launch-lms-actions-unstable -o IdentitiesOnly=yes \
     /tmp/unstable-candidate/candidate.json \
     root@UNSTABLE_IP:/tmp/candidate.json
   # On the unstable host:
   cd /opt/launch-lms
   bash setup.sh unstable /tmp/candidate.json
   ```
   Setup prompts for the domain, a separate administrator password, and DNS token.
   Unstable also prompts for the production domain (to reject nested cookie
   domains) and shared tester HTTP credentials. These are an outer access gate;
   each tester still signs into their own Launch LMS account afterward. A
   successful tester prompt issues a secure 12-hour gate cookie shared by the
   unstable apex and organization subdomains, leaving the Authorization header
   available for the signed-in application's Bearer token.
7. Setup writes private `.env`, `.deployment-environment`, and unstable lock files,
   then pulls the pinned app, starts dependencies, downloads the embedding model,
   migrates, starts the app/Caddy, verifies services, and backfills search. Secrets
   are not printed. Host/DNS credentials are excluded from the generated app env.
   If setup fails after writing configuration, fix the reported issue and use
   `bash deploy.sh`; setup refuses to overwrite an existing installation.
8. Remove the bootstrap registry login and revoke the temporary PAT:
   ```bash
   docker logout ghcr.io
   ```
   Keep the DNS token: Caddy needs it for renewals. Verify HTTPS at the apex and an
   organization subdomain after DNS propagation/TLS issuance; internal health
   checks do not prove public DNS/TLS. Log in, upload/open a file, search, and
   open a collaborative activity. Test simultaneous production/unstable sessions.

If registry setup fails with `unauthorized`, authenticate to `ghcr.io` with a
token that has `read:packages` and access to the `launch-lms` package, then run
`bash deploy.sh`. Do not rerun `setup.sh`: after it writes `.env`, its
`Already initialized` refusal protects those secrets. If a manual Compose command
reports `LAUNCHLMS_IMAGE` is unset, load the pinned image selection first:

```bash
cd /opt/launch-lms
source scripts/load-release-env.sh
docker compose ps
```

Initial `502` responses during `deploy.sh` are expected while the app becomes
ready; the command succeeds only after its retries and full verification pass.
For wildcard TLS, look for `certificate obtained successfully` for
`*.ENVIRONMENT_DOMAIN` in `docker compose logs caddy`. DNS pointing at a droplet
does not cause Caddy to serve that domain until `.env` and `Caddyfile.active`
name it.

## Move two GoDaddy domains to DigitalOcean DNS

Use one registrable domain for production and a different one for unstable. In
the commands and checklists below, substitute your actual values for
`PROD_DOMAIN`, `UNSTABLE_DOMAIN`, `PROD_IP`, and `UNSTABLE_IP`.

For the current installation those values are:

| Target | Domain | IPv4 |
| --- | --- | --- |
| Production | `life2launch.app` | `146.190.134.27` |
| Unstable | `life2launch.dev` | `137.184.34.50` |

Do the unstable domain first. Its setup is reversible and does not change the
current production site. Move the production domain only after unstable has
passed its owner checks and you have scheduled the production domain cutover.

1. In DigitalOcean, open **Networking → Domains** and add both apex domains.
   Create these records in each zone:

   | Type | Hostname | Value |
   | --- | --- | --- |
   | `A` | `@` | That environment's droplet IPv4 |
   | `A` | `*` | That environment's droplet IPv4 |
   | `A` | `www` | Optional; the wildcard already covers it |

   Add AAAA records only when the matching droplet and firewall actually support
   IPv6. The wildcard is required for organization hosts such as
   `life2launch.life2launch.dev`; an apex record alone is insufficient.
2. Before delegating an actively used domain, copy every record it needs into
   DigitalOcean: MX, SPF/DKIM/DMARC TXT records, verification TXT records, CAA,
   and intentional subdomains. Create new provider-issued records when moving
   email to a new sending domain; a DKIM key or verification value for the old
   domain does not automatically authorize the new one. The old
   `_acme-challenge` TXT values are expired one-time certificate proofs and should
   not be copied. Caddy creates and removes current challenge records through the
   DigitalOcean API. Delegating nameservers moves authority for the whole zone;
   GoDaddy's old zone stops answering once caches expire.
3. In GoDaddy, open **Domain Portfolio → the domain → DNS → Nameservers**. Choose
   **I'll use my own nameservers** and enter:

   ```text
   ns1.digitalocean.com
   ns2.digitalocean.com
   ns3.digitalocean.com
   ```

   Save and complete GoDaddy's identity check. Repeat for the other domain when
   its zone is ready. GoDaddy notes that global propagation can take up to 48
   hours; DigitalOcean says it is commonly 30 minutes to several hours.
4. Verify delegation and address records from a machine outside the droplets:

   ```bash
   dig +short NS PROD_DOMAIN
   dig +short A PROD_DOMAIN
   dig +short A test-org.PROD_DOMAIN
   dig +short NS UNSTABLE_DOMAIN
   dig +short A UNSTABLE_DOMAIN
   dig +short A test-org.UNSTABLE_DOMAIN
   ```

   All three DigitalOcean nameservers should appear, and apex/wildcard queries
   should resolve to the intended environment's IP. Do not initialize Caddy
   until the DNS token can edit these DigitalOcean zones.
   If `dig` is unavailable on Ubuntu, install `dnsutils`, or use
   `getent ahostsv4 DOMAIN` for a basic address check.
5. Initialize unstable against `UNSTABLE_DOMAIN`, complete the login/org/file/
   search/collaboration checks, and rehearse one refresh. Its separate domain
   prevents production cookies from being sent to the test environment.
6. To move the existing production deployment to `PROD_DOMAIN` while retaining
   its current image, disable the production GitHub environment, take a snapshot,
   and follow **Migrate the existing production droplet** below. Change only the
   domain/origin/cookie/collaboration values in `.env`; preserve the release
   lock, database, content volume, JWT key, and other production secrets. Set
   `LAUNCHLMS_LEGACY_DOMAIN` to the old production base domain to redirect its
   apex, `www`, and organization hosts to their equivalents on the new domain.
   Run `bash deploy.sh`, then verify the new apex and an org subdomain before
   enabling production automation again.
7. Keep the old production site active until the scheduled cutover. Save the old
   `.env`; if verification fails, restore it and run `bash deploy.sh` to return
   traffic to the old domain. With `LAUNCHLMS_LEGACY_DOMAIN` configured, Caddy
   obtains certificates for both domains and permanently redirects old apex and
   `www` requests to the new apex while preserving organization subdomains and
   request paths. After the new domain passes, announce it and update OAuth
   callbacks, webhook destinations, email links, bookmarks, and external
   integration allowlists.

Official references: [DigitalOcean DNS delegation](https://docs.digitalocean.com/products/networking/dns/getting-started/dns-registrars/),
[DigitalOcean domain setup](https://docs.digitalocean.com/products/networking/dns/getting-started/quickstart/),
and [GoDaddy custom nameservers](https://www.godaddy.com/help/change-my-domain-nameservers-664).

## Connect GitHub Actions to each droplet

Create a separate no-passphrase Actions key for each environment on a trusted
workstation:

```bash
ssh-keygen -t ed25519 -C 'launch-lms-actions-unstable' \
  -f ~/.ssh/launch-lms-actions-unstable
ssh-keygen -t ed25519 -C 'launch-lms-actions-production' \
  -f ~/.ssh/launch-lms-actions-production
```

Install only the matching public line on each droplet. If your normal
administration key already connects, run this from the workstation:

```bash
cat ~/.ssh/launch-lms-actions-unstable.pub | \
  ssh -i ~/.ssh/YOUR_ADMIN_KEY -o IdentitiesOnly=yes root@UNSTABLE_IP \
  'umask 077; mkdir -p /root/.ssh; cat >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys'
```

Alternatively, use the DigitalOcean console to open
`/root/.ssh/authorized_keys`, paste the complete single `.pub` line on a new
line, save it, and run `chmod 600 /root/.ssh/authorized_keys`.

Test before configuring GitHub:

```bash
ssh -i ~/.ssh/launch-lms-actions-unstable -o IdentitiesOnly=yes root@UNSTABLE_IP
```

For private infra repos, this inbound Actions key and the repo checkout's
outbound read-only deploy key are separate responsibilities. The infra repo is
currently public, so its droplet checkout needs no GitHub deploy key. Verify the
SSH host fingerprint directly from the droplet console:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

In the infra repository, open **Settings → Environments**, create `unstable` and
`production`, and open each environment. Under **Environment secrets**, set:

| Secret | Value |
| --- | --- |
| `DROPLET_HOST` | That environment's droplet address |
| `DROPLET_USER` | User with Docker access and ownership of `/opt/launch-lms` |
| `DROPLET_SSH_KEY` | Private inbound deploy key |
| `DROPLET_SSH_FINGERPRINT` | Verified `SHA256:...` host fingerprint |

`DROPLET_SSH_KEY` is the private file without `.pub`, including its
`-----BEGIN OPENSSH PRIVATE KEY-----` and ending line. The host fingerprint is
the output from `/etc/ssh/ssh_host_ed25519_key.pub`; it is not the deploy key
fingerprint.

Under **Environment variables**, create `DEPLOY_ENABLED=false`. Avoid shared
repository-level droplet secrets. After bootstrap, set it to `true` for unstable,
run **Actions → Deploy environment → Run workflow → unstable**, and verify the
host. Then set the app repository variable `UNSTABLE_DEPLOY_ENABLED=true` under
**app Settings → Secrets and variables → Actions → Variables**. That app switch
is repository-level; each infra `DEPLOY_ENABLED` switch is environment-level.
Enable production only after its migration and public checks pass.

An unstable build dispatch carries only candidate metadata; the environment
secrets determine the host. The host rejects an environment mismatch and stale
candidate run IDs. Changes to infra alone automatically deploy production when
enabled; manually run **Deploy environment → unstable** to apply infra changes
to unstable with its existing candidate lock.

The deployment holds a host lock across checkout and deployment, requires a clean
tracked working tree, and checks out the exact workflow infra SHA. It never runs
`git reset --hard origin/main`. Manual operations below share the same host lock.

## Migrate the existing production droplet

Do not rerun setup or recreate/delete its volumes. First disable production
Actions and take an independent database/content backup using the existing
installation's configuration. Ensure its app image and migration schema are
known and that any new migrations have a recovery plan.

1. Preserve any intentional tracked server changes in the infra repository, then
   deploy the merged revision containing these scripts **and a valid digest lock**.
   Resolve dirty tracked files explicitly; the new workflow refuses to discard them.
2. In `/opt/launch-lms`, create the environment marker:
   ```bash
   printf 'production\n' > .deployment-environment
   chmod 600 .env .deployment-environment
   ```
3. Confirm `.env` has `LAUNCHLMS_ENV=prod`,
   `LAUNCHLMS_DEVELOPMENT_MODE=false`,
   `LAUNCHLMS_INTERNAL_API_URL=http://localhost/api/v1/`,
   `LAUNCHLMS_INTERNAL_BACKEND_URL=http://localhost:9000`, the production
   default organization slug in `NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG`, and blank
   `NEXT_PUBLIC_LAUNCHLMS_API_URL` for same-origin browser API calls. Preserve the
   existing database connection, passwords, JWT key, and content settings.
   Correct a legacy `redis://redis:6379/launchlms` URL to
   `redis://redis:6379/0` (Redis databases are numeric).
   Use literal values (no `${...}` references) in this file. Set
   `NEXT_PUBLIC_LAUNCHLMS_TOP_DOMAIN` to your environment's base domain.
4. Verify the existing content volume name with `docker volume ls`. The default
   remains `launch-lms_content_data`. If your old Compose project used a different
   name, set `CONTENT_VOLUME_NAME` to that exact existing volume before deploying.
5. Authenticate for the manual pull, run `bash deploy.sh`, and verify externally.
   Ollama/model initialization and the first search backfill can take time. Check
   available disk/RAM beforehand. Re-enable production Actions after this passes.

## Refresh unstable from production

This is a **one-way replacement**, not continuous synchronization. Password hashes,
accounts, org memberships, and resource data come from the snapshot. Testers log
in again with their password as of the snapshot. OAuth-only accounts need a test
password provisioned through supported account administration; no production
OAuth credentials or active sessions are copied.

The helper supports local Compose PostgreSQL and filesystem content. It refuses
S3 or managed database sources; export those to an independent test storage setup
before extending the helper. It pauses production app/Caddy while copying to
keep database and files consistent. Schedule a short maintenance window and
ensure no other writers access the database/storage.

1. On production, create a new private snapshot directory **outside the repo**:
   ```bash
   cd /opt/launch-lms
   bash scripts/snapshot.sh --maintenance-window /root/launch-snapshots/2026-09-08
   ```
   The helper restarts the same production containers even if export fails.
   Only a completed snapshot has `snapshot.json` with file checksums. This bundle
   contains private user data/password hashes: restrict access and transfer it
   over SSH, not Git or public artifact storage.
2. Copy that directory to the unstable host using your operator SSH access:
   ```bash
   scp -r /root/launch-snapshots/2026-09-08 root@UNSTABLE_IP:/root/launch-snapshots/
   ```
   Create the destination parent privately first if necessary.
3. Announce the test-data reset. On unstable:
   ```bash
   cd /opt/launch-lms
   bash scripts/refresh-unstable.sh --replace-test-data /root/launch-snapshots/2026-09-08
   ```
   It validates checksums, archive paths, separate domains/keys, and the local
   target before creating a fresh database/content volume. It restores, migrates,
   clears API tokens/custom-domain mappings/guest sessions, disables and clears
   payment/SSO connections, revokes invitations/join links, removes organization
   scripts, and rewrites URL authorities from the production domain to the test
   domain (including org subdomains). Emails and password hashes stay unchanged.
   Browser-visible external links are still external links; URLs embedded inside
   binary uploads cannot be rewritten by this database sanitizer.
4. Cutover saves the old `.env`, switches database/content, rotates the unstable
   JWT secret, clears test Redis, and deploys/verifies. On a cutover failure it
   attempts to restore the old configuration; old DB and content are retained.
   Inspect logs and verify after any failure. No failed refresh deletes old data.
5. Check `.deploy-state/last-refresh.json` for snapshot time, source, and new
   database/volume names. Tell testers the snapshot time and that their previous
   test accounts, passwords, progress, and uploads were replaced. Keep feedback
   outside this environment. Check a copied account and a representative file.

Refresh is operator-triggered initially, not cron-driven: it replaces tester
work and briefly pauses production for export. After a few rehearsals you can
schedule an agreed maintenance/reset window. Monitor disk growth; retained
snapshots and old test databases/volumes need an explicit retention policy.
Never use `docker compose down -v` to clean up this installation.

## Updates, diagnosis, and recovery

Routine updates are described in the app guide. On either host:

```bash
cd /opt/launch-lms
bash scripts/verify-deploy.sh
cat .deploy-state/deployed-release.json
# Logs; do not paste secrets or private user data from logs into tickets.
# Source the validated lock to set image/Compose selection for manual commands:
source scripts/load-release-env.sh
docker compose logs --tail 100 launch-lms
```

`bash deploy.sh` (or `bash scripts/repair.sh`) reapplies the currently checked-out
infra/selected image, migrations, model initialization, readiness, and backfill.
It requires GHCR authentication if the image is private and a pull is needed.
The script preserves `attempted-release.json`, `previous-release.json`, and
`deployed-release.json`; success is recorded only after all checks/backfill pass.
It does not automatically delete old images.

If a migration fails, the previous app is not replaced, but schema changes may
already have occurred. If runtime checks fail after replacement, production
requires operator recovery; do not interpret the running container as success.
For a schema-compatible rollback, copy the previous good production lock into
`release.lock.json` in a new infra PR and merge it. For unstable, under the host
lock, restore a known-good `dev` candidate lock and re-run deploy. Pause automatic
unstable updates while investigating so a new build cannot replace your rollback.

For an incompatible migration, stop traffic and restore the pre-deploy database
and matching content with the previous app. Rehearse this on a disposable host
first. For the supported local filesystem production layout, the restoration
sequence is:

1. Disable Actions for the target and acquire `.deploy.lock`. Stop Caddy/app.
2. Keep a copy of the failed DB/files if needed for diagnosis. Create a fresh
   local database, restore `database.dump` with `pg_restore --no-owner --no-acl
   --exit-on-error`, and restore `content.tar.gz` into a fresh named volume.
3. Set `.env` to the restored database connection and `CONTENT_VOLUME_NAME`, and
   use the snapshot's verified `release.json` as the production lock via a new
   infra recovery PR. **Do not run the newer image's migrations on the restored
   old schema.** Deploy the matching previous image and verify externally.
4. Record the recovery point/data loss window and retain failed data until the
   recovery is confirmed. Use the installation's original JWT/domain settings;
   the unstable sanitizer must never run against production.

To undo a successful unstable refresh, first disable its Actions. Under
`.deploy.lock`, restore `.deploy-state/before-refresh-TIMESTAMP.env` to `.env`,
run `python3 scripts/render-app-env.py`, then `docker compose up -d launch-lms
caddy` with the correct sourced lock and verify. This returns to the retained
pre-refresh database/content. Test Redis sessions were cleared and will not be
restored. If the image changed afterward, also restore its matching candidate.

Domain changes are container-based: update all domain/origin/cookie/collab values
in `.env`, update DNS, and run `bash deploy.sh`. It regenerates `Caddyfile.active`
and reloads the containerized Caddy; do not edit a host `/etc/caddy/Caddyfile` or
use `systemctl reload caddy` for this Compose installation.

## Local checks

```bash
python3 -m pip install 'SQLAlchemy>=2.0,<2.1'  # preferably in a virtualenv
python3 -m unittest discover -s tests -v
for script in deploy.sh setup.sh scripts/*.sh; do bash -n "$script"; done
```

Tests cover lock/environment mismatch, stale dispatch rejection, snapshot
checksums/path traversal, separate domains/keys, fresh storage/key rotation, and
sanitization retaining password hashes while clearing integration credentials.
The optional disposable PostgreSQL/network rehearsal uses a locally built app:

```bash
IMAGE=launch-lms:local bash tests/rehearse-copy.sh
```

Run `actionlint` on `.github/workflows/*.yaml` and validate both Compose configs
with a synthetic `.env`/lock before publishing changes. Real cloud DNS/TLS, SSH
secrets, package permissions, and production signoff remain deployment checks.
