# Versioned environment topology

`launch-lms.yaml` is the non-secret deployment-time source of truth for public
origins, DNS names, environment isolation, and the operations-surface location.
Application and control-plane code must consume runtime variables generated from
this contract; it must not contain Life2Launch hostnames.

The accepted topology is:

- production tenants: `{organization}.life2launch.app`;
- unstable tenants: `{organization}.unstable.life2launch.app`;
- operations portal and embed origin: `life2launch.dev`.

OpenTofu creates `unstable` and `*.unstable` A records in the existing
`life2launch.app` DigitalOcean zone. Caddy obtains the nested wildcard certificate
through the DigitalOcean DNS challenge. Both production and unstable deployments
must use host-only authentication cookies before nested unstable traffic is
enabled; the topology validator enforces that contract. Changing cookie scope
does not delete existing browser cookies. Prior to moving unstable beneath the
production domain, expire legacy `Domain=.life2launch.app` auth cookies for
existing users and verify in a real browser that they are absent when visiting
the new unstable origin. Account for the 30-day refresh-token lifetime; do not
assume updating the cookie-setting code alone accomplishes this migration.

Existing org switching depends on shared session cookies. Host-only mode also
requires a short-lived, one-time authenticated handoff when a user follows a
link from one organization host to another; verify Google and enterprise SSO,
direct links, logout, and default-org redirects. `session_handoff_verified` and
`unstable.cutover_approved` must remain false until their exact commit, CI, and
TLS evidence is recorded beside them. New managed installations and the nested
unstable deployment refuse to proceed until those gates are reviewed and
enabled, even though OpenTofu can prepare the DNS records beforehand.

While the existing unstable installation still uses the operations-domain apex,
its generated Caddy configuration serves only
`/.well-known/launch-lms-domain-preflight` on the nested unstable apex and
wildcard. The endpoint returns 204 and all other paths return 404. This allows
the repository deployment workflow to provision and renew the DNS-challenge
certificate and prove public DNS/TLS without exposing the application under a
domain it has not adopted. The preflight-only site disappears automatically
when the runtime domain is migrated.

The protected **Unstable domain cutover** workflow is the only supported live
migration path. It requires an explicit target-domain confirmation, rewrites
only the reviewed non-secret runtime keys, retains a mode-0600 pre-cutover
environment snapshot, deploys under the shared unstable lock, and verifies
public TLS, the tester access gate, apex and tenant routing, and the `.dev`
redirect. It then provisions one deterministic synthetic acceptance account
whose password is derived inside the host from the installation secret, and uses
it to verify a real source-host login, one-use target handoff, ticket replay
rejection, legacy-cookie expiry, host isolation, and logout. The account is
stable across reruns and exists only in the disposable unstable database.
Credentials and tokens are never printed or transferred to the Actions runner.
Any apply or verification failure restores the snapshot and redeploys the former
domain automatically. The same workflow exposes an explicit rollback action; do
not edit the host `.env` manually.

The `.dev` apex remains on the old unstable host while
`dns.operations_apex_cutover` is false. The control-plane deployment script also
refuses to start in that state. After nested unstable passes routing, TLS, login,
tenant-isolation, and rollback checks:

1. change `operations_apex_cutover` to `true` in a reviewed pull request;
2. dispatch the protected host workflow with the existing `.dev` apex record ID
   so OpenTofu adopts rather than duplicates it (the workflow refuses an apply
   without the ID if the record is not already in state);
3. review and apply the plan, which moves the apex to the operations Droplet;
4. dispatch the protected control-plane deployment.

Unstable setup writes the final operations URL but leaves both surface enable
flags `false`; turn them on only after the control-plane and iframe have passed
their separate parity checks. Existing `.dev` wildcard records, if any, remain
on the old unstable host until a separate reviewed cleanup; they are not needed
for the operations apex and must not be pointed at the control plane.

Changing the topology is a deployment-config change and runs infrastructure
validation. Secrets remain in the protected GitHub `operations` Environment.

During the host-only migration, environment setup writes
`NEXT_PUBLIC_LAUNCHLMS_LEGACY_COOKIE_DOMAIN=life2launch.app`. The application
uses it only in host-only mode to expire former parent-domain auth and routing
cookies. Keep it configured for at least the old 30-day refresh-token lifetime,
verify cookie absence in browser evidence, then remove it from both deployments.
