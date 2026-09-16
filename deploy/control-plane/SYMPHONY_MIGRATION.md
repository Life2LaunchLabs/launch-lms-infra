# Symphony host migration and recovery

This runbook moves the single worker only after the control-plane host, private
status network, PostgreSQL backup, and runner secrets have passed their own
deployment checks. Do not combine this operation with an application release.

## Preflight and OOM gate

1. On the current host, run `python3 scripts/symphony-state.py inspect` and save
   the output in the private change record. It contains image/resource/exit state,
   but no environment or secret values.
2. Treat `oom_killed: true`, nonzero cgroup OOM counters, repeated restarts, or
   memory saturation in host
   metrics as a failed gate. Capture the task, phase, peak RSS, host available
   memory, and relevant redacted logs. Resolve the workload or host capacity
   before migration; do not raise concurrency.
3. Provision at least 8 GiB RAM on the destination and set
   `SYMPHONY_MEMORY_LIMIT=4g` in `/etc/launch-symphony/compose.env`. This sizing
   follows the observed 1.9 GiB Next process plus roughly 850 MiB idle worker state
   with operating headroom. Do not interpret swap as replacement capacity.
4. Verify no issue is in the middle of an owner-approved Merge operation. Record
   active Jira issue keys, workspace names, PR heads, rendered workflow hash, and
   the control-plane database backup revision.

## Pause and snapshot

From the current release checkout:

```bash
docker compose -f docker-compose.symphony.yml exec symphony touch /home/node/PAUSED
docker compose -f docker-compose.symphony.yml restart symphony
curl -fsS http://127.0.0.1:8788/api/v1/state
docker compose -f docker-compose.symphony.yml stop symphony
python3 scripts/symphony-state.py snapshot --output /var/backups/launch-operations
python3 scripts/symphony-state.py verify /var/backups/launch-operations/symphony-home-TIMESTAMP.tar.gz
```

Copy the archive and adjacent JSON manifest through the encrypted operator
channel. Verify the manifest checksum again on the destination. Retain the source
volume and stopped container until the rollback window closes.

## Restore and validate

Create the destination volume explicitly. Restore as root inside a networkless,
read-only helper container, then restore ownership to uid/gid 1000. Never restore
into a running worker or use a volume belonging to another Compose project.

```bash
docker volume create launch-symphony_symphony-home
docker run --rm --network none --read-only \
  -v launch-symphony_symphony-home:/restore \
  -v /var/backups/launch-operations:/backup:ro \
  alpine:3.20 sh -c 'cd /restore && tar -xzf /backup/symphony-home-TIMESTAMP.tar.gz && chown -R 1000:1000 .'
docker compose --env-file /etc/launch-symphony/compose.env \
  -f docker-compose.symphony.yml up -d --no-deps symphony
```

The restored `PAUSED` marker must keep dispatch disabled. Confirm the authenticated
control-plane can read the sanitized status endpoint, the rendered workflow metadata
matches the source, every recorded workspace exists, Git remotes contain no embedded
credentials, and unfinished work resumes from its existing branch/workpad. Validate
one non-delivery synthetic handoff and the repository browser scenario before
removing `PAUSED`. Then run:

```bash
python3 scripts/symphony-state.py preflight
```

The command must report `passed: true`: healthy worker, persistent volume, no OOM
events, worker limit of at least 4 GiB, host memory of at least 8 GiB, and peak
usage below 85% of the cgroup limit. A failed gate blocks dispatch; preserve its
JSON output with the migration evidence.

## Resume and rollback

Remove `PAUSED`, restart, and watch one existing eligible task without increasing
concurrency. Confirm the installation token is short-lived, the exact-SHA review
record reaches PostgreSQL, and no completed task is redispatched.

If any invariant fails, stop the destination worker, preserve its logs/volume for
diagnosis, and restart the still-paused source. Remove the source `PAUSED` marker
only after its workspace and Jira/PR state are reconciled. DNS or control-plane
traffic can move independently; never run both workers against the same eligibility
queue.
