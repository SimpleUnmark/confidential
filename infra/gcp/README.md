# Google Confidential Space deployment

The operator reviews and runs all cloud mutations, image pushes, Terraform
plans/applies, and state operations. Assistant-side gcloud usage is read-only.
The existing project and Terraform-state bucket are the only infrastructure
bootstrap exceptions; all other service resources are managed by Terraform.

For the repository split, read [migration notes](../../docs/repository-migration.md)
first. The already-applied foundation keeps its original bucket, state prefix,
and resource addresses. Do not recreate it.

## 1. Foundation

Follow [foundation/](foundation/README.md) for APIs and Artifact Registry. Both
Terraform stacks pin 1.15.8 and use checked-in backend/public configuration.
From this public repository's root:

```bash
cd infra/gcp/foundation
terraform init
terraform plan
```

The operator applies only after reviewing the plan. Production uses Frankfurt
(`europe-west3`) and registry
`europe-west3-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential`.

## 2. Public release and verified image transfer

Configure **this public repository's** GitHub `production` environment:

- Environment variable `DOCKERHUB_USERNAME=simpleunmark`
- Environment secret `DOCKERHUB_TOKEN` with the required repository push access

Run **Release confidential workload** on reviewed source. It runs CI first,
builds an AMD64 Distroless image, pushes it to Docker Hub, and attests both the
image and browser client tarball. Download the manifest and approve its exact
source commit, workflow identity, and image digest. Use a new tag for a new
candidate. Prerelease semver tags do not publish `latest`; deployment always
uses a digest, never a mutable tag.

```bash
gh attestation verify \
  oci://docker.io/simpleunmark/simpleunmark-confidential@sha256:DIGEST \
  --repo SimpleUnmark/confidential
```

Only after verification, the operator configures local registry authentication
and copies the immutable manifest. These are release operations, not Terraform
resource provisioning; no GCP key is stored in GitHub.

```bash
gcloud auth configure-docker europe-west3-docker.pkg.dev

SOURCE_IMAGE=docker.io/simpleunmark/simpleunmark-confidential@sha256:DIGEST
DESTINATION_IMAGE=europe-west3-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential:release-COMMIT

docker buildx imagetools create --prefer-index=false --tag "$DESTINATION_IMAGE" "$SOURCE_IMAGE"
docker buildx imagetools inspect "$DESTINATION_IMAGE"
```

**Require the destination digest to equal the verified source digest.** Stop if
they differ. Set the runtime image reference to the destination `@sha256:...`,
not the temporary transfer tag. Verify provenance against the public source
repository; a registry copy does not change that source identity.

## 3. Runtime

Follow [terraform/](terraform/README.md) for the private-IP Confidential Space
VM, least-purpose IAM, VPC, NAT, HTTPS load balancer, certificate, and DNS-only
Cloudflare record. From the runtime directory use plain `terraform init`,
`terraform plan`, and, after review, `terraform apply`.

Credentials go in the ignored `terraform.tfvars`; Cloudflare authentication
comes from the local `CLOUDFLARE_API_TOKEN`. Never publish those files, state,
plans, or `.terraform/`. The legacy `deploy-confidential-space.sh` is retained
for reference only and must not be used alongside this Terraform deployment.

v1 intentionally uses no Secret Manager, Cloud KMS, STS, or Confidential Space
workload identity pool. The DeepInfra key and shared HMAC key are present in
ordinary VM metadata and Terraform state. IAM principals allowed to read them
can obtain those credentials. Neither credential decrypts the request-scoped
HPKE payloads; the HMAC key can forge authorizations and accounting receipts.

The production payload fixes the receipt URL, allowed browser origin,
DeepInfra destination/model, and attestation audience. No container environment
or command overrides are permitted. Changing those constants requires a new
reviewed image. Scratch media uses a 1 GiB tmpfs mounted at `/tmp/simpleunmark`.

## 4. Client/application handoff

The private website is a separate deployment; this repository does not contain
its Dockerfile, database, or payment implementation. Supply these public policy
values from the approved release and Terraform outputs:

- `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_IMAGE_DIGESTS`
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
