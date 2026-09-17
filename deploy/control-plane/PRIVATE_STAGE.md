# Private operations stage

The first portal stage uses the protected `Stage control plane privately` workflow on
`main` and the `operations` GitHub Environment. It installs the two protected
runtime files, checks out the exact workflow commit, creates the private status
network, connects the already running Symphony worker, migrates PostgreSQL, and
starts only PostgreSQL, API, and web. It does not start Caddy, publish a host port,
change DNS, or enable the Launch LMS embed.

Set `OPERATIONS_READ_ONLY=true` in `OPERATIONS_CONTROL_PLANE_ENV`. The API denies
every non-read `/api/v1/` request except local session logout before route code
runs, including operator dispatch and all embed writes. The stage script probes
that gate and checks API, web, and Symphony status reachability from inside the
private stack. The portal still requires GitHub OAuth when public routing is
later enabled.

An operator can create the two files without hand-formatting them by running
`python3 scripts/setup-operations-runtime.py` from a local infra checkout after
creating the OAuth App and installing the GitHub App. It prompts for the PEM file
and Jira/OAuth values, generates fresh PostgreSQL and session secrets, validates
the DNS-independent topology, and uploads the two environment secrets directly
through `gh`. It prints neither the values nor the generated files.

Versioned SQL is under `postgres/`. The API image applies pending files in a
single transaction before API startup and rejects a changed migration checksum.
The first migration is safe for a previously initialized database. For a local
protected database backup on the host:

```bash
bash scripts/operations-db.sh backup /var/backups/launch-operations/operations-YYYYMMDD.dump
bash scripts/operations-db.sh restore-test /var/backups/launch-operations/operations-YYYYMMDD.dump
```

`restore-test` imports into a newly named disconnected database, checks the
migration ledger, then removes that test database. Retain encrypted off-host
copies and a recorded restore drill before treating this as a recovery system.

The design reference for the read-only portal is the existing operations shell
in `apps/control-plane/web/src`, with the owner's September 17 delivery plan as
the content and state brief. Review desktop 1440×900 and phone 390×844, including
login, running, retrying, empty, stale, unavailable, keyboard focus, and long
issue identifiers. A browser screenshot comparison and live OAuth/provider
acceptance remain required before claiming UI handoff.

The deployment view deliberately says **Incomplete** until a protected host
attestation, candidate artifact, and successful workflow run are reconciled.
No status-poll response is used as a durable attempt record.
