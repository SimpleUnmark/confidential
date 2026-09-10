# Frankfurt to Belgium: operator-run migration

This is the historical regional migration runbook. For current image publication,
approval and runtime selection use [release-security.md](../../docs/release-security.md):
CI publishes directly to Belgium; the digest comes from the approved policy,
not an `image_reference` tfvars assignment. Do not repeat an already completed migration.

**Updated:** [Secret Manager setup](secret-manager-migration.md) now supersedes
the old-image runtime instructions. The registry copy may finish, but deploying
the VM requires a NEW Secret Manager-capable image and credential setup first.

Target project: `simple-unmark-prod`. Target region/zone: `europe-west1` /
`europe-west1-b`. Hostname: `confidential.simpleunmark.com` (unchanged).

This is an additive registry migration before the first runtime deployment:

- Keep the existing Frankfurt `workloads` repository and its images intact.
- Create a Belgium `workloads` repository with Terraform.
- Copy the approved release without rebuilding it; verify the destination digest.
- Point the runtime at Belgium, then review its plan separately.
- Keep `gs://simple-unmark-prod-tfstate` in Frankfurt with the same state prefixes.

Repository locations are immutable. Changing the old resource's location would
replace it, which `prevent_destroy` correctly blocks. Instead, the foundation
uses `for_each` and a `moved` block to adopt the old address as
`google_artifact_registry_repository.workload["europe-west3"]`. The new Belgium
instance is separate. No manual `terraform state mv`, import, state-bucket
migration, or targeted apply is needed for the existing managed foundation.

All commands below are run by the operator. No cloud changes have been applied
by preparing this configuration. The live inventory check encountered expired
gcloud authentication; verify the inventory before applying.

## 1. Refresh authentication and check existing resources

```bash
source ~/.zshrc
gcloud auth login

gcloud artifacts repositories list --project=simple-unmark-prod
gcloud compute instances list --project=simple-unmark-prod
gcloud artifacts docker images list \
  europe-west3-docker.pkg.dev/simple-unmark-prod/workloads --include-tags
```

Expect a Frankfurt `workloads` registry and the approved release below. There
should be no workload VM yet. If a workload VM already exists, stop before the
runtime apply: it needs a separate replacement/outage review. If Terraform
reports expired Application Default Credentials, run
`gcloud auth application-default login` yourself and retry; gcloud CLI and
Terraform credentials are separate. Never paste credentials or raw state into chat.

## 2. Create Belgium while retaining Frankfurt

```bash
cd /Users/vladimir/Projects/simpleunmark/confidential/infra/gcp/foundation
terraform init
terraform plan
```

For the previously applied production foundation, expect:

- An address move from `google_artifact_registry_repository.workload` to
  `google_artifact_registry_repository.workload["europe-west3"]`.
- **4 to add, 0 to change, 0 to destroy** if neither migration has been
  applied: Belgium registry, Secret Manager API, and two secret containers.
  If Belgium is already applied, expect only the three Secret Manager additions.
- An updated `image_repository` output pointing at Belgium.
- No API removals, registry replacement, IAM changes, VM, or load balancer.

Stop if the plan proposes destroying/replacing Frankfurt, creating it again,
or unexpectedly changing other resources. Do not disable `prevent_destroy` to
force the migration. When the plan is correct:

```bash
terraform apply
terraform output
```

Review the newly generated apply plan again before approving. The active image
repository should be:

```text
europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential
```

## 3. Copy the approved image, preserving the digest

The previously approved release is commit
`f18c08cc5846f9c589dd06203b1b82db0325a17c` and image digest
`sha256:c4b30b69dda546261c6e76df19fe7d55dcf08ff2f07645818868530eeef2f7c9`.
If a newer release has been approved, verify its provenance and substitute its
digest/tag consistently instead. Do not select an image merely because it has
a `latest` tag.

Verify the original public provenance, then configure authentication for both
registries. Stop if verification fails:

```bash
gh attestation verify \
  oci://docker.io/simpleunmark/simpleunmark-confidential@sha256:c4b30b69dda546261c6e76df19fe7d55dcf08ff2f07645818868530eeef2f7c9 \
  --repo SimpleUnmark/confidential

gcloud auth configure-docker europe-west3-docker.pkg.dev,europe-west1-docker.pkg.dev
```

With Docker running, copy the existing Frankfurt image. These literal
references avoid zsh's special colon modifiers on unbraced variables:

```bash
docker buildx imagetools create --prefer-index=false \
  --tag europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential:release-f18c08cc \
  europe-west3-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@sha256:c4b30b69dda546261c6e76df19fe7d55dcf08ff2f07645818868530eeef2f7c9

docker buildx imagetools inspect \
  europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential:release-f18c08cc
```

Require the reported digest to equal the approved digest above. Stop on any
mismatch. A registry copy is not a new build, and the original GitHub provenance
remains the source of trust; this command does not promise to copy associated
attestation/referrer artifacts into the destination registry.

## 4. Prepare the runtime with Secret Manager

Follow [the Secret Manager runbook](secret-manager-migration.md). The old
`f18c08cc` image copied above is retained only as a historical release; do not
deploy it with the new secret-reference metadata.

Production region/zone already target Belgium. All non-secret configuration now
lives in checked-in `terraform/production.auto.tfvars`, including the new image
digest and explicit secret version numbers. Do not create a private tfvars file
or pass credential values into Terraform. Sizing remains `n2d-standard-2`
until a separate reviewed memory-sizing change.

Review the runtime plan after secrets and the new image are ready. Apply only
when ready to create the VM, then follow the runtime guide for DNS and end-to-end
testing. The new image requires a new browser digest allowlist.

## 5. Retire Frankfurt only after Belgium is verified

Frankfurt is intentionally retained in
`foundation/production.auto.tfvars` under `retained_repository_regions`. It
remains Terraform-managed and protected against accidental destruction; this
is not an orphaned repository. It incurs storage charges for retained images.

After the Belgium workload passes end-to-end tests, inventory all Frankfurt
images and any other consumers. Copy anything still required, then prepare a
separate reviewed Terraform retirement of **only** the Frankfurt registry.
Removing the retained region alone is deliberately blocked by `prevent_destroy`.
Do not remove state entries or delete the repository with gcloud as a shortcut,
and do not delete or recreate the Frankfurt state bucket.

For rollback before runtime deployment, keep the repositories and return the
runtime region/zone and digest-pinned image path to Frankfurt. After the VM is
deployed, changing its region requires a separately reviewed runtime replacement.
