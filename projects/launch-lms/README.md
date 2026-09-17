# Launch LMS project adapter

`project.yaml` is the versioned registration contract used by every platform
service. Repository-owned policy is fetched from the exact application commit;
it is not copied here. Values in this directory contain no credentials.

Changes to tracker mappings, required checks, origins, environments, token keys,
or enabled modules require review because they alter an enforcement boundary.

The key files under `keys/` are public Ed25519 verification keys. Their private
halves belong only to the Launch LMS unstable deployment secret boundary; they
are never mounted into the control plane. The manifest identifies both current
and next keys so rotation can overlap without interrupting active testers.
