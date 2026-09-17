# Private operations stage

The first portal stage uses the protected `Stage control plane privately` workflow on
`main` and the `operations` GitHub Environment. It renders runtime files from
individual protected secrets, checks out the exact workflow commit, creates the private status
network, connects the already running Symphony worker, migrates PostgreSQL, and
starts only PostgreSQL, API, and web. It does not start Caddy, publish a host port,
change DNS, or enable the Launch LMS embed.

The renderer fixes `OPERATIONS_READ_ONLY=true`. The API denies
every non-read `/api/v1/` request except local session logout before route code
runs, including operator dispatch and all embed writes. The stage script probes
that gate and checks API, web, and Symphony status reachability from inside the
private stack. The portal still requires GitHub OAuth when public routing is
later enabled.

Add the individual secrets listed in [OWNER_SETUP.md](OWNER_SETUP.md) through
GitHub Settings. The workflow validates them, renders private mode `0600`
files on the runner, and transfers those files to the host. The files are
runtime artifacts only; rotate a credential by editing its one secret and
rerunning the protected stage or deployment workflow.

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
login, running, retrying, blocked, empty, stale, unavailable, keyboard focus, and long
issue identifiers. A browser screenshot comparison and live OAuth/provider
acceptance remain required before claiming UI handoff.

The deployment view deliberately says **Incomplete** until a protected host
attestation, candidate artifact, and successful workflow run are reconciled.
No status-poll response is used as a durable attempt record.
