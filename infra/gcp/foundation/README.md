# Foundation: APIs, image registries, and secret containers

This is the first Terraform apply. It enables required project APIs, manages
Docker repositories and two Secret Manager containers (no secret values), and
configures keyless GitHub publishing. The active Belgium workload repository has
public read; the retained Frankfurt repository is unchanged.
The operator-approved project-wide domain-sharing exception is managed in
`public-registry-policy.tf`. It permits external IAM grants throughout
`simple-unmark-prod`; only the Belgium registry is granted public read here.
The organization and other projects are not modified. See the
[policy permissions and propagation notes](../../../docs/release-security.md#1-gcp-foundation).
It requires no workload image, DeepInfra key, DNS token, or VM.
The separate runtime stack in `../terraform` is applied only after
an image exists. Both use the same GCS state bucket with distinct prefixes.

The operator runs all commands that change cloud resources or Terraform state.
The project and state bucket are infrastructure bootstrap exceptions; the
operator also manages the workload's DNS record separately.
Image builds and publication are release operations, not Terraform resources.

## Existing Frankfurt deployment: migrate to Belgium

Follow [the migration runbook](../belgium-migration.md). Production now targets
Belgium (`europe-west1`) and retains the Frankfurt registry (`europe-west3`) for
rollback. A `moved` block adopts the original registry address into a
region-keyed resource without recreating it. Expect the Belgium registry (if not
already created), Secret Manager API, and two secret containers to be added,
zero deletions, and no VM. Do not import or manually move state for this
already-managed registry. The bucket remains in Frankfurt.

## 1. Create the state bucket once

The existing project is `simple-unmark-prod`. Run these yourself after reviewing
them (choose another globally unique bucket name if this one is unavailable):

```bash
gcloud storage buckets create gs://simple-unmark-prod-tfstate \
  --project=simple-unmark-prod \
  --location=europe-west3 \
  --uniform-bucket-level-access \
  --public-access-prevention

gcloud storage buckets update gs://simple-unmark-prod-tfstate --versioning
```

Keep bucket access restricted to the deployment operators. The runtime state
and saved plans must remain private. New configurations contain no credential
payloads; historical state may contain them if the old runtime was applied.
Public access prevention does not remove permissions already granted to project members.
GCS encrypts stored objects and the Terraform backend supports state locking;
versioning allows recovery of older state objects. Do not set a bucket retention
lock: Terraform must be able to delete its temporary lock object.

## 2. Review and apply the foundation

Terraform is pinned to `1.15.8` in `.terraform-version` for version managers
such as tfenv, and `required_version` rejects any other CLI version. Provider
versions are locked separately in `.terraform.lock.hcl`.

The checked-in `backend.tf` selects the state bucket and foundation prefix.
`production.auto.tfvars` automatically supplies the non-secret project and
Belgium region settings plus the retained Frankfurt repository. No backend or
variable-file flags are needed.

From the repository root, enter this directory and run:

```bash
cd infra/gcp/foundation
terraform init
terraform plan
```

On existing production, expect publishing IAM, the federation pool/provider,
the dedicated publisher service account, public registry read, and any missing
identity APIs. No VM, repository, or secret replacement is intended. On a fresh
project, the registries and secret containers are also created.
When satisfied, run `terraform apply`. It generates a fresh plan;
review that plan again before typing `yes` at the confirmation prompt:

```bash
terraform apply
terraform output
```

If a registry was already created outside Terraform, import that exact resource
before planning. Do not recreate or delete it:

```bash
terraform import \
  'google_artifact_registry_repository.workload["europe-west1"]' \
  projects/simple-unmark-prod/locations/europe-west1/repositories/workloads
```

Only run the import if the repository exists. Already-enabled APIs can be
adopted by the API resources during apply.

## 3. Configure release governance, publish, then deploy

Follow [the release-security runbook](../../../docs/release-security.md) and apply
`infra/github` before merging/enabling the release workflows. It configures
GitHub environment variables from this stack's outputs and protects publication
and approval. CI publishes directly to GCP and Docker Hub without a GCP key.


Follow [Secret Manager setup](../secret-manager-migration.md) to populate the two
containers directly, outside Terraform. Secret replicas are located in Belgium
with Google-managed encryption; no customer-managed KMS is required.
Publish the reviewed Secret Manager-capable release image and record its digest.
Build/push commands are run by the operator or release CI; Terraform does not
build containers or store Docker credentials. Then follow `../terraform/README.md`.

The runtime stack owns the service account, repository-scoped image-pull IAM,
Confidential Space VM, networking, load balancer, and certificate. The operator
creates the DNS record from the runtime's public IP output. The runtime reads
the registry created here. Apply the foundation first and keep
both states separate. Never use routine `-target` applies to bypass this order.

For an existing deployment using the old combined configuration, migrate the
four `google_project_service.required` state entries into this stack before
applying either stack, and migrate local state to GCS with `init -migrate-state`.
Do not run a fresh-state apply over an existing managed deployment.
