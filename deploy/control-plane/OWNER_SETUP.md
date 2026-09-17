# Owner setup scratchpad (not a cutover runbook)

This is the small set of account-level inputs the repository cannot create for you. Do **not** paste tokens, private keys, or complete environment files into a PR, issue, chat, or this document. Keep `life2launch.dev` pointed at the existing unstable host until the nested unstable deployment has been tested and the reviewed cutover gate is enabled.

## Live checkpoint — 2026-09-17

- State bucket preparation/versioning succeeded in [run 35146626233](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35146626233). The temporary full-access Spaces key has been revoked in DigitalOcean and its two GitHub Environment secrets removed; only the bucket-scoped state key remains.
- The existing operations Droplet `601077988` was adopted into versioned remote state without replacement. [Cloud apply 35149808494](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35149808494) created its firewall and dedicated project after the nested DNS records were created in the preceding partial apply. The [follow-up plan](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35149910044) reported **No changes**.
- [Host configuration](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35151083811) and the separate [host verification](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35151695299) passed. The host is configured but the control-plane services have **not** been started.
- `unstable.life2launch.app` and `life2launch.unstable.life2launch.app` resolve to the existing unstable server `137.184.34.50`. The guarded [nested cutover run 35185623351](https://github.com/Life2LaunchLabs/launch-lms-infra/actions/runs/35185623351) passed public TLS/routing plus live login, one-use handoff, replay rejection, legacy-cookie expiry, host isolation, and logout against Launch LMS `9cfe46e6b4c8758e74b8ef9d5b8dab4a2b89bfce` and infra `c44a0503a8dab8027a9984aaf853676eb4d663e1`.
- `life2launch.dev` still resolves to the unstable server and now redirects to `unstable.life2launch.app`. The operations host remains configured but the control-plane services have not been started.

Next gate: complete owner browser acceptance on the nested unstable hostname, create the least-privilege GitHub App described below, and add the individual protected secrets listed below. Then approve the `.dev` apex gate in a separate PR, adopt and move its existing DNS record through the protected host workflow, and deploy the control plane. No new DigitalOcean token or Spaces key is requested.

## Do now

1. In DigitalOcean, create a **private SFO3 Spaces bucket** for OpenTofu state (for example `l2l-operations-tfstate`; the globally unique name is your choice). Do not enable the CDN or public listing. Create a Spaces access key restricted to this bucket with read/write/delete access; state locking needs to remove lock files. In the `Life2LaunchLabs/launch-lms-infra` GitHub **operations** Environment, set `OPERATIONS_STATE_BUCKET` to the bucket name (variable) and `SPACES_ACCESS_KEY_ID` and `SPACES_SECRET_ACCESS_KEY` (secrets). Versioning requires bucket-configuration access: create a **temporary full-access Spaces key** and set it separately as `SPACES_VERSIONING_ACCESS_KEY_ID` and `SPACES_VERSIONING_SECRET_ACCESS_KEY` for the one-time `prepare-state` action. Revoke that broad key and delete its two GitHub secrets immediately after `prepare-state` succeeds. The normal plan/apply jobs use only the bucket-limited key.
2. Create a scoped DigitalOcean personal access token capable of managing the existing operations Droplet, firewall, project, and DNS records in the `life2launch.app` and `life2launch.dev` zones. Set it as the `DIGITALOCEAN_TOKEN` secret in that same GitHub Environment. Keep its expiry and rotation date in your password manager. The protected host workflow already knows the Droplet ID (`601077988`); **do not** manually recreate or repoint anything.
3. In the Life2LaunchLabs GitHub organization, register an OAuth App for the operator portal. Set homepage to `https://life2launch.dev` and its **only callback** to `https://life2launch.dev/api/v1/auth/github/callback`. Disable wildcard callback matching if offered. Save its ID and secret as the separate protected secrets `OPERATIONS_GITHUB_OAUTH_CLIENT_ID` and `OPERATIONS_GITHUB_OAUTH_CLIENT_SECRET`.
4. Establish two separate Atlassian identities/tokens: one for BOT delivery and one for FEED feedback. Grant each only the project permissions it needs. For scoped service-account tokens, set `OPERATIONS_JIRA_BASE_URL=https://api.atlassian.com/ex/jira/<cloudId>`; the human site URL `https://henrydker.atlassian.net` does not work with those tokens. Store the email/token pairs as the separate protected secrets listed below. Do not put them in the worker environment.

## GitHub App for the read-only portal

The OAuth App in step 3 signs **operators** into the portal. A GitHub App is a different identity: it lets the portal request short-lived installation tokens for repository automation. It is **not needed** for state preparation, OpenTofu plan/apply, or host configuration; there is nothing more to create for this today.

Create one organization-owned GitHub App with **Only select repositories → `launch-lms` and `launch-lms-infra`**. Give it repository **Actions: Read-only** and **Contents: Read-only**, with no organization or administration permissions and no webhooks. Save its App ID, installation ID, and generated PEM key as separate protected secrets. The connector restricts each minted token to one repository and the needed permission. If workflow dispatch or app-repository PR editing is later enabled, review an explicit permission expansion first. See [GitHub's installation-token scoping](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).

The `operations` Environment already has the host IP, SSH identity, source CIDRs, and pinned host key. There is no need to re-enter those unless rotating them.

## Individual protected runtime secrets

In `Life2LaunchLabs/launch-lms-infra` → **Settings → Environments → operations → Environment secrets**, choose **Add secret** for each row. The PEM may already be present. Generate the two new random values locally and keep a recovery copy in your password manager; do not paste any value into chat or a repository file.

| Secret name | Value |
| --- | --- |
| `OPERATIONS_POSTGRES_PASSWORD` | A new random URL-safe string of at least 32 characters (`A–Z`, `a–z`, `0–9`, `_`, `-`); one value supplies PostgreSQL and its API URL. |
| `OPERATIONS_SESSION_SECRET` | A different new random string of at least 32 characters. |
| `OPERATIONS_GITHUB_OAUTH_CLIENT_ID` | OAuth App client ID. |
| `OPERATIONS_GITHUB_OAUTH_CLIENT_SECRET` | OAuth App client secret. |
| `OPERATIONS_GITHUB_APP_ID` | Numeric GitHub App ID from its General page. |
| `OPERATIONS_GITHUB_APP_INSTALLATION_ID` | Numeric installation ID from the app's installation URL. |
| `OPERATIONS_GITHUB_APP_PRIVATE_KEY` | Entire downloaded GitHub App PEM, including BEGIN/END lines. |
| `OPERATIONS_JIRA_BASE_URL` | `https://henrydker.atlassian.net` for ordinary API tokens; `https://api.atlassian.com/ex/jira/<cloudId>` for scoped service-account tokens. |
| `OPERATIONS_JIRA_DELIVERY_EMAIL` | BOT Jira account email. |
| `OPERATIONS_JIRA_DELIVERY_TOKEN` | BOT Jira account token. |
| `OPERATIONS_JIRA_FEEDBACK_EMAIL` | FEED Jira account email. |
| `OPERATIONS_JIRA_FEEDBACK_TOKEN` | FEED Jira account token, distinct from BOT's. |

To rotate one value, replace its single Environment secret and rerun the protected stage or deployment workflow. The workflow trims accidental whitespace around single-line values, rejects embedded line breaks, and assembles mode `0600` runtime files just before transfer. Its versioned topology supplies the fixed domain and host IP; there are no duplicate password fields to update.

## After the repository gates are green

1. Dispatch `Control-plane host` with `action=prepare-state` on the protected infra branch. Confirm the run reports bucket versioning **Enabled**, then revoke the temporary full-access Spaces key and remove `SPACES_VERSIONING_ACCESS_KEY_ID` and `SPACES_VERSIONING_SECRET_ACCESS_KEY` from GitHub. Dispatch `action=plan` to review proposed new resources. Before the first apply, dispatch `action=adopt-plan` with `adopt_droplet_id=601077988`; this imports the existing Droplet into remote state without applying cloud changes and exposes its real post-adoption diff for review. Only after that plan is accepted, dispatch `action=apply` (the same Droplet ID may be supplied idempotently). For the later approved `.dev` cutover, the workflow discovers and adopts the existing apex record; leave its optional record-ID recovery override empty.
2. Dispatch `action=configure`, then `action=verify`. This configures the host through Ansible; no manual SSH bootstrap is part of the normal path.
3. In `launch-lms-infra` → **Settings → Environments → operations → Environment secrets**, add each secret in the table below. The protected private-stage workflow then renders the files and starts PostgreSQL, API, and web without public Caddy or a DNS cutover.
4. Only after the nested unstable host has passed TLS, login, org-switching, logout, cookie isolation, and rollback checks should we approve `operations_apex_cutover` in `deploy/environments/launch-lms.yaml`. The reviewed `.dev` DNS adoption and control-plane deploy then happen via workflows. Do **not** switch `life2launch.dev` in the DigitalOcean UI now.

Official setup references: [Spaces buckets](https://docs.digitalocean.com/products/spaces/getting-started/quickstart/), [Spaces key scopes](https://docs.digitalocean.com/products/spaces/how-to/manage-access/), [Spaces versioning](https://docs.digitalocean.com/products/spaces/how-to/enable-versioning/), [Spaces Terraform state](https://docs.digitalocean.com/products/spaces/reference/terraform-backend/), [GitHub Environment secrets](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), [GitHub OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app), and [Atlassian scoped-token gateway](https://support.atlassian.com/atlassian-cloud/kb/401-unauthorized-error-when-service-account-accesses-jira-or-confluence-api/).
