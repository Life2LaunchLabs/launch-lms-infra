# State bucket bootstrap

This local-state OpenTofu module adopts the existing private SFO3 state bucket,
enables object versioning, and verifies the bucket has no configuration drift.
It exists because the state bucket must be protected before the primary remote
backend can safely initialize.

The protected host workflow is the only supported entry point. Its temporary
bootstrap state lives only on the ephemeral Actions runner and contains no
application data or credentials. The bucket has `prevent_destroy`, is never
created or destroyed by the workflow, and is imported before every operation.

Normal plan/apply jobs use the bucket-scoped state key. `verify_versioning.py`
calls only `GetBucketVersioning`, which DigitalOcean permits for read-capable
keys; it deliberately avoids provider bucket reads such as `GetBucketAcl` that
would unnecessarily require full bucket-administration access.
