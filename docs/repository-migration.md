# Repository split: operator checklist

This checkout contains a selected source export, not the private website's Git
history. No GitHub transfer, push, release, or cloud mutation is performed by the
file migration. Review the complete public diff before the first push.

## Terraform continuity

This section records the original repository split. The subsequent
[Belgium regional migration](../infra/gcp/belgium-migration.md) supersedes its
Frankfurt deployment target and no-change plan expectation. The bucket and
state prefixes below still apply; do not repeat the original split migration.

The production project stays `simple-unmark-prod`, the region stays Frankfurt,
and the state bucket stays `simple-unmark-prod-tfstate`. Foundation state uses
`confidential/foundation`; runtime uses `confidential/runtime`. Resource names
and Terraform addresses are unchanged by the split. **Do not import or recreate
the foundation**, which has already been applied.

Old `.terraform/` directories and any private local variable files were not
exported. They remain in the old private checkout, ignored by Git. Do not apply
from that old location again. In the public checkout's relevant Terraform
directory, the operator runs:

```bash
terraform init
terraform plan
```

Initialization uses the same remote state. The foundation should show no
infrastructure changes due to this move. Stop and inspect if it proposes to
recreate the existing registry. No `state mv`, `import`, or backend migration
is needed just because the repository path changed.

Create the runtime's ignored `terraform.tfvars` only when deploying the service.
Never publish state, plans, credential files, `.terraform/`, or real `.env` files.
Project IDs, bucket names, DNS names, and provider lockfiles are public config,
not credentials. Review every future change to `production.auto.tfvars`.

## GitHub and release identity

1. Ensure this repository is public and branch/release permissions are restricted
   to trusted maintainers. Enable review protection for workflow and security
   changes. Review public contributions without exposing release secrets.
2. In this repository's `production` environment, set variable
   `DOCKERHUB_USERNAME=simpleunmark` and secret `DOCKERHUB_TOKEN` with push access
   only to the required image repository. Credentials in the private repository
   do not automatically transfer to this newly created repository.
3. Push reviewed source to this repository, wait for CI, then run **Release
   confidential workload** on the approved commit/ref. Use a new release tag;
   do not move the previous candidate tag to a different commit.
4. Verify image and client provenance against `SimpleUnmark/confidential`, not
   the private repository. The earlier failed-attestation image is not an
   approved release; a new build has a new revision and may have a new digest.
5. Copy the verified OCI digest to the existing Frankfurt registry, and only
   then set the runtime's image reference. The website's allowlisted digest
   must match the new deployed release.

The private repository keeps its existing history, application migrations, and
cross-application review notes. Check its Git remote and hosting integration
after any GitHub organization transfer; file moves do not update those settings.

## Client package handoff

The private website vendors `@simpleunmark/confidential-client` as a versioned
tarball, whose bytes are integrity-pinned in its pnpm lockfile. No npm account or
sibling checkout is required for deployment. The initial local tarball is an
unpublished extraction snapshot, **not a GitHub-attested release**.

After the first public release, verify and import its attested tarball into the
website, update the dependency/lockfile, and run web integration tests and a
production build. Record the verified public commit and tarball digest in the
private vendor README. Bump the client package version for subsequent changes;
never silently replace an already approved version. Protocol compatibility is
tested on both sides, but image releases and website rollouts remain separate.
