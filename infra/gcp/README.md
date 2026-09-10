# Google Confidential Space deployment

The operator reviews and runs all cloud mutations, image pushes, Terraform
plans/applies, and state operations. Assistant-side gcloud usage is read-only.
The existing project and Terraform-state bucket are the only infrastructure
bootstrap exceptions. DNS is also managed manually by the operator; the other
service resources are managed by Terraform.

For the repository split, read [migration notes](../../docs/repository-migration.md)
first. The already-applied foundation keeps its original bucket and state prefix.
The [Belgium migration](belgium-migration.md) uses a declarative address move to
preserve the Frankfurt repository and creates a second repository in Belgium.
Do not recreate the Frankfurt repository or move the state bucket.

## 1. Foundation

Follow [foundation/](foundation/README.md) for APIs and Artifact Registry. Both
Terraform stacks pin 1.15.8 and use checked-in backend/public configuration.
From this public repository's root:

```bash
cd infra/gcp/foundation
terraform init
terraform plan
```

The operator applies only after reviewing the plan. Production targets Belgium
(`europe-west1`) and registry
`europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential`.
The existing Frankfurt registry is retained for rollback during cutover.

## 2. Keyless CI publication and reviewed approval

Follow [the release-security runbook](../../docs/release-security.md). After the
foundation apply, enter `infra/github` and run plain `terraform init`,
`terraform plan`, and `terraform apply` yourself. It configures GitHub publishing
variables and protected environments. Keep the existing Docker Hub username/token.

Run **Release confidential workload** from reviewed `main`. CI builds once and
publishes identical single-platform image digests to Docker Hub and public-read
Belgium Artifact Registry using GitHub OIDC federation. No GCP key or manual copy
is needed. CI signs image/client provenance and an unapproved candidate manifest.

Approve exact digests through a `releases/approved-workloads.json` PR and the
separate **Attest approved release policy** workflow. Run `pnpm release:verify`
and export the signed policy with `pnpm release:export-web ../simpleunmark`.
Publishing alone cannot select the VM image or expand the browser allowlist.

## 3. Runtime

Follow [terraform/](terraform/README.md) for the private-IP Confidential Space
VM, least-purpose IAM, VPC, NAT, HTTPS load balancer, and certificate. From the
runtime directory use plain `terraform init`,
`terraform plan`, and, after review, `terraform apply`.

Follow [the Secret Manager guide](secret-manager-migration.md) before runtime
deployment. Upload credential values directly to Secret Manager. Checked-in
`production.auto.tfvars` holds numeric secret version pins; image selection comes
from `releases/approved-workloads.json`;
no private tfvars, Cloudflare token, or zone ID is required. After apply, use `terraform output` to obtain `public_ip`, then
manually point the `confidential.simpleunmark.com` A record at that IP with
proxying disabled (DNS-only). Never publish credential files, state,
plans, or `.terraform/`. The legacy `deploy-confidential-space.sh` is disabled to prevent accidental plaintext-metadata deployment.

The workload retrieves its DeepInfra key and shared HMAC key directly from
Secret Manager with the attached VM service account. Secret values never enter
Terraform or VM metadata. No customer-managed KMS, STS, or runtime workload
identity pool is needed. This is IAM-based access, not attestation-gated release:
administrators with IAM/identity control can still obtain the credentials.
Neither credential decrypts request-scoped HPKE payloads.

The production payload fixes the receipt URL, allowed browser origin,
DeepInfra destination/model, and attestation audience. No container environment
or command overrides are permitted. Changing those constants requires a new
reviewed image. Scratch media uses a 1 GiB tmpfs mounted at `/tmp/simpleunmark`.

## 4. Client/application handoff

The private website is a separate deployment; this repository does not contain
its Dockerfile, database, or payment implementation. Supply these public policy
values from the approved release and Terraform outputs:

- the checked-in, verified release-policy snapshot (image digest allowlist)
- `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_GCP_PROJECT_NUMBERS`
- `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_SERVICE_ACCOUNTS`
- `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_HARDWARE_MODELS=GCP_AMD_SEV`
- `NEXT_PUBLIC_CONFIDENTIAL_SERVER_ORIGIN`
- `NEXT_PUBLIC_CONFIDENTIAL_ATTESTATION_AUDIENCE`

The website's adapter passes those build-time values to the public
[client library](../../packages/client/README.md). It verifies Google's JWT
signature and audience, fresh key-binding nonce, image digest, project, service
account, Secure Boot, debug-disabled state, production support attributes,
disabled memory monitoring, and absent command/environment overrides before
encrypting content. It never adopts identity values advertised by the workload.

The HTTPS load balancer terminates TLS outside the TEE. Confidential requests
and responses also have application-layer encryption; Private mode does not.
DeepInfra still receives plaintext text over HTTPS, and website-delivered
JavaScript remains trusted. Review the complete
[threat boundary](../../apps/confidential-server/README.md#threat-boundary).

## 5. Acceptance and rotation

Follow the [deployment checklist](../../docs/deployment-checklist.md). Health
checks alone do not test attestation, decryption, receipt settlement, or provider
access. Actual cleaning needs the compatible authorization/receipt backend.

The current service is a single instance with per-process ephemeral keys,
attestation throttling, and one media job at a time. Do not add replicas until
request affinity or shared-state coordination is designed. Image changes
replace the VM and can briefly interrupt availability. Publish a client policy
accepting old and new approved digests first, rotate the workload, test, then
remove the old digest from a later website build.
