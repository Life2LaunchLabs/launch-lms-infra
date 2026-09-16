# Symphony memory incident — 2026-09-13

## Status

Confirmed capacity failure on the legacy unstable host. This record contains no
credentials, application data, Jira content, or workspace contents. The live worker
was inspected read-only on 2026-09-16 and was not paused, restarted, or modified.

## Evidence

- Host: 3.8 GiB usable RAM, no swap.
- Worker cgroup limit: 2,411,724,800 bytes (approximately 2.25 GiB).
- Current worker cgroup: 3 OOM events and 2 OOM kills.
- Kernel records: four cgroup kills at 17:09, 17:13, 17:33, and 17:36 UTC.
- Killed process: `next-server`; observed anonymous RSS was approximately 1.5–1.9 GiB.
- The last two events occurred after the current worker container started.
- At inspection the idle worker used approximately 850 MiB with 28 processes.
- Persistent state measured approximately 1.9 GiB of workspaces and 203 MiB of
  Codex state. Symphony reported no running, blocked, or retrying tasks.

The cgroup, rather than the host kernel's global memory pool, enforced the kills.
Recreating the container would reset counters without resolving the capacity issue.

## Resolution gate

- Keep concurrency at one.
- Do not run the full local browser/application stack on the legacy worker; retain
  GitHub Actions as the fallback for large browser and image checks.
- Use a dedicated operations host with at least 8 GiB RAM and a 4 GiB Symphony
  cgroup. The sizing covers the observed Next process plus idle worker usage with
  headroom; it is an initial acceptance floor, not a concurrency guarantee.
- Run a synthetic repository browser scenario while dispatch remains paused, then
  require `python3 scripts/symphony-state.py preflight` to pass.
- Preserve the preflight JSON and peak measurement with migration evidence.
- Re-evaluate the limit from ten varied task records before adding another worker.

The source volume remains the rollback authority until the destination completes
snapshot verification, continuation, one-task delivery, and the rollback window.
