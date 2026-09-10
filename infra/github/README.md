# GitHub release governance

This third, small stack manages GitHub only: existing `production` environment,
new `release-approval` environment, main branch protection and the four public
GCP publishing variables from foundation outputs. It uses the same private state
bucket with prefix `confidential/github`. It neither builds images nor deploys VMs.

First apply `../gcp/foundation`. Then follow the complete
[release-security runbook](../../docs/release-security.md#one-time-setup-operator-runs-these).
Use `GITHUB_TOKEN` in your shell (never tfvars), and plain `terraform init`,
`terraform plan`, `terraform apply` in this directory. Only the operator applies.

Default protections work for the existing solo owner: required PR/CI, no direct
or force pushes, manual owner approval for publishing and for signing policies.
This is not a two-person/multisig control. See the runbook before enabling
`independent_review_required` and selecting additional release reviewer IDs.

When updating the provider lockfile, record both CI and local platform hashes:

```bash
terraform providers lock -platform=linux_amd64 -platform=darwin_arm64
```

CI keeps `-lockfile=readonly`; do not disable checksum verification to work around
a platform mismatch. This command updates only the local lockfile, not cloud state.
