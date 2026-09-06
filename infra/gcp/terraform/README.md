# Terraform: Confidential Space v1

Apply [`../foundation`](../foundation/README.md) first: it manages project API
enablement, private Artifact Registry repositories, and secret containers. This stack reads that
registry and prepares the CPU Confidential mode runtime with Secret Manager,
without customer-managed Cloud KMS, STS, or a Confidential Space workload identity
pool. It creates:

- a custom VPC and private subnet;
- one private-IP N2D Confidential Space VM using SEV, Secure Boot, vTPM, and an
  immutable digest-pinned payload image;
- a least-purpose workload service account with Confidential Computing workload
  and Artifact Registry read roles;
- secret-scoped Secret Manager access for the workload service account;
- Cloud Router and Cloud NAT for outbound provider and receipt calls;
- an external global HTTPS load balancer and Google-managed certificate;
- a firewall rule that exposes port 8080 only to Google's load-balancer and
  health-check ranges.

DNS is managed manually by the operator. No Cloudflare provider, zone ID, or
API token is required. Terraform still creates the HTTPS certificate and
load balancer, and outputs the IP to publish in DNS.

The operator runs all commands that change cloud resources or Terraform state.
The GCS backend is mandatory; the bucket is created once using the foundation
README. Its state prefix is `confidential/runtime`, separate from foundation.

Terraform is pinned to `1.15.8` in `.terraform-version`; `required_version`
enforces the same exact version even without a version manager. Provider
versions are locked separately in `.terraform.lock.hcl`. The checked-in
`backend.tf` selects the bucket and runtime prefix. `production.auto.tfvars`
automatically loads the public project, Belgium region/zone, and hostname.
The state bucket remains in Frankfurt. For the existing registry cutover, follow
[the Belgium migration](../belgium-migration.md) before applying this stack.

The HTTPS load balancer terminates TLS outside the TEE. Confidential-mode text
is already HPKE-encrypted in the browser and response events are encrypted to
the same browser context. Keep HTTPS because it still protects capabilities,
attestation tokens, origins, and transport integrity. Private mode is sent over
ordinary HTTPS from the web app to the same workload.

## Credentials and state

Credential values never pass through Terraform. Foundation creates the two
Secret Manager containers; the operator uploads their values directly. Runtime
looks up container metadata only and grants the VM service account accessor
roles on those two secrets. The Python service retrieves explicit numeric
versions at startup. VM metadata contains **references**, not secret payloads.

All production inputs, including the image digest and secret version numbers,
belong in the checked-in `production.auto.tfvars`. No private `terraform.tfvars`
is needed. Still keep Terraform state and plans private. If the old configuration
was applied with real secrets, old state/object versions may contain them: rotate
those credentials and handle historical state separately.

Follow [the Secret Manager runbook](../secret-manager-migration.md) before
deploying. Secret access is service-account IAM based, not attestation-gated.
Privileged administrators can still obtain credentials through IAM/identity
control. Neither credential decrypts recorded HPKE payloads.

## Deployment order

1. Apply the foundation, publish the workload image to its Artifact Registry
   repository, and copy its immutable `@sha256:...` reference. The runtime's
   image-pull role is scoped to this one repository.
2. Follow [the Secret Manager runbook](../secret-manager-migration.md) to upload
   both values, configure the website's matching HMAC key, and build a new
   Secret Manager-capable image. The older plaintext-metadata image is incompatible.
3. Set the approved new image digest and numeric secret versions in
   `production.auto.tfvars`. Its placeholder deliberately fails validation
   until a new release is chosen. Remove obsolete credential assignments from
   any old private tfvars yourself; never copy them into the public file.
   Initialize and review:

   ```bash
   cd infra/gcp/terraform
   terraform init
   terraform plan
   ```

4. Apply only after reviewing the plan. `terraform apply` generates a fresh
   plan; review it again before typing `yes` at its confirmation prompt:

   ```bash
   terraform apply
   ```

5. Run `terraform output` and manually create/update the `A` record for
   `confidential.simpleunmark.com` using `public_ip`. In Cloudflare, use name
   `confidential`, DNS-only (grey cloud), and TTL Auto. This IPv4-only stack does
   not need an AAAA record; check for conflicting existing records for this
   hostname. Google's managed certificate remains provisioning until DNS
   resolves correctly and validation completes. Inspect backend health and
   certificate status before proceeding.
6. Build and deploy the web app with:

   - `CONFIDENTIAL_SERVER_PUBLIC_URL` from the Terraform output;
   - `CONFIDENTIAL_SHARED_SECRET` matching the selected Secret Manager version;
   - `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_IMAGE_DIGESTS` from
     `expected_image_digest`;
   - `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_GCP_PROJECT_NUMBERS` from
     `project_number`;
   - `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_SERVICE_ACCOUNTS` from
     `workload_service_account`;
   - `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_HARDWARE_MODELS=GCP_AMD_SEV`;
   - `NEXT_PUBLIC_CONFIDENTIAL_SERVER_ORIGIN` set to the origin of
     `confidential_server_public_url`;
   - `NEXT_PUBLIC_CONFIDENTIAL_ATTESTATION_AUDIENCE` set to the fixed audience
     baked into the workload image.

The `NEXT_PUBLIC_*` policy is embedded in the browser bundle at build time.
During a release, first build a web bundle accepting both old and new digests,
then apply the new workload image. Terraform replaces the single stateless VM
so the launcher reads the new digest at boot. Drain outstanding work first,
expect a brief v1 outage, verify the attested digest in the browser, and finally
remove the old digest from a later web build.

## Deliberate v1 limits

- This is one VM in one zone, not a high-availability service.
- The request-scoped encryption key and its attestation token live only in one
  Python process. The browser's attestation and clean calls must reach that same
  instance. Do not add another backend until routing affinity or shared
  attestation state is designed and reviewed.
- Changing `image_reference` or either secret version replaces the stateless VM. The
  default `deletion_protection = false` permits that safe rotation; enabling
  deletion protection intentionally blocks replacement until it is disabled.
- The Distroless payload image fixes the production origin, receipt URL,
  DeepInfra host/model, and attestation audience. Changing them requires a new
  reviewed image and digest, not a runtime environment override.
- DeepInfra still receives plaintext in both available modes. Confidential AI
  on a TDX-backed H100 confidential GPU is a separate future stack.
- The website operator controls browser JavaScript. An independently distributed
  verifier is needed to remove that operator from the frontend trust boundary.

After every apply, check `/v1/info` for the released revision and perform one
Confidential clean. The browser's verification details must show the newly
approved image digest and `GCP_AMD_SEV` before the old digest is removed.
