# Hosted environment locks

Each hosted environment pins an exact tested application image digest. Unstable
is updated automatically from a verified dev candidate. Production changes only
through a reviewed lock PR and reuses the candidate digest without rebuilding.

Server credentials remain in GitHub Environments. The control plane may dispatch
and observe protected workflows but never receives SSH deployment credentials.
