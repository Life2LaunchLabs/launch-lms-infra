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
`unstable.cutover_approved` start false. New managed installations and the
nested unstable deployment refuse to proceed until those gates are reviewed and
enabled, even though OpenTofu can prepare the DNS records beforehand.

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
