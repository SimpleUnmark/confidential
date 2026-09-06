# Foundation: APIs and image registry

This is the first Terraform apply. It enables four project APIs and creates the
private Docker repository. It requires no workload image, DeepInfra key, DNS
token, or VM. The separate runtime stack in `../terraform` is applied only after
an image exists. Both use the same GCS state bucket with distinct prefixes.

The operator runs all commands that change cloud resources or Terraform state.
The project and state bucket are the only infrastructure bootstrap exceptions.
Image builds and publication are release operations, not Terraform resources.

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
and saved runtime plans contain the two workload credentials. Public access
prevention does not remove permissions already granted to project members.
GCS encrypts stored objects and the Terraform backend supports state locking;
versioning allows recovery of older state objects. Do not set a bucket retention
lock: Terraform must be able to delete its temporary lock object.

## 2. Review and apply the foundation

Terraform is pinned to `1.15.8` in `.terraform-version` for version managers
such as tfenv, and `required_version` rejects any other CLI version. Provider
versions are locked separately in `.terraform.lock.hcl`.

The checked-in `backend.tf` selects the state bucket and foundation prefix.
`production.auto.tfvars` automatically supplies the non-secret project and
Frankfurt region settings. No backend or variable-file flags are needed.

From the repository root, enter this directory and run:

```bash
cd infra/gcp/foundation
terraform init
terraform plan
```

Review the plan: four API enablement resources and one registry, no VM or load
balancer. When satisfied, run `terraform apply`. It generates a fresh plan;
review that plan again before typing `yes` at the confirmation prompt:

```bash
terraform apply
terraform output
```

If a registry was already created outside Terraform, import that exact resource
before planning. Do not recreate or delete it:

```bash
terraform import \
  google_artifact_registry_repository.workload \
  projects/simple-unmark-prod/locations/europe-west3/repositories/workloads
```

Only run the import if the repository exists. Already-enabled APIs can be
adopted by the API resources during apply.

## 3. Publish, then deploy the runtime

Publish the reviewed release image into the registry and record its digest.
Build/push commands are run by the operator or release CI; Terraform does not
build containers or store Docker credentials. Then follow `../terraform/README.md`.

The runtime stack owns the service account, repository-scoped image-pull IAM,
Confidential Space VM, networking, load balancer, certificate, and Cloudflare A
record. It reads the registry created here. Apply the foundation first and keep
both states separate. Never use routine `-target` applies to bypass this order.

For an existing deployment using the old combined configuration, migrate the
four `google_project_service.required` state entries into this stack before
applying either stack, and migrate local state to GCS with `init -migrate-state`.
Do not run a fresh-state apply over an existing managed deployment.
