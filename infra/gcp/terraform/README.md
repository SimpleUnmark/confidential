# Terraform: Confidential Space v1

Apply [`../foundation`](../foundation/README.md) first: it manages project API
enablement and the private Artifact Registry repository. This stack reads that
registry and prepares the CPU Confidential mode runtime without Secret
Manager, Cloud KMS, STS, or a Confidential Space workload identity pool. It
creates:

- a custom VPC and private subnet;
- one private-IP N2D Confidential Space VM using SEV, Secure Boot, vTPM, and an
  immutable digest-pinned payload image;
- a least-purpose workload service account with Confidential Computing workload
  and Artifact Registry read roles;
- Cloud Router and Cloud NAT for Artifact Registry, DeepInfra, Google
  attestation, and receipt callbacks;
- an external global HTTPS load balancer and Google-managed certificate;
- a firewall rule that exposes port 8080 only to Google's load-balancer and
  health-check ranges.
- a DNS-only Cloudflare A record in the existing simpleunmark.com zone.

The operator runs all commands that change cloud resources or Terraform state.
The GCS backend is mandatory; the bucket is created once using the foundation
README. Its state prefix is `confidential/runtime`, separate from foundation.

Terraform is pinned to `1.15.8` in `.terraform-version`; `required_version`
enforces the same exact version even without a version manager. Provider
versions are locked separately in `.terraform.lock.hcl`. The checked-in
`backend.tf` selects the bucket and runtime prefix. `production.auto.tfvars`
automatically loads the public project, Frankfurt region/zone, and hostname.

The HTTPS load balancer terminates TLS outside the TEE. Confidential-mode text
is already HPKE-encrypted in the browser and response events are encrypted to
the same browser context. Keep HTTPS because it still protects capabilities,
attestation tokens, origins, and transport integrity. Private mode is sent over
ordinary HTTPS from the web app to the same workload.

## Important state and metadata warning

`deepinfra_api_key` and `confidential_shared_secret` are marked sensitive, but
Terraform sensitivity only redacts ordinary CLI output. Their plaintext values
are stored in Terraform state and ordinary GCE instance metadata. Use a
restricted, encrypted remote state backend, never commit credentials in a
`.tfvars` file, and
limit principals that can read the state or Compute instance metadata. This is
the explicit simplicity/security tradeoff of v1.

Anyone who obtains the DeepInfra key can use the provider account. Anyone who
obtains the HMAC key can forge cleaning capabilities or accounting receipts.
Neither key decrypts recorded Confidential-mode payloads because HPKE uses a
request-scoped P-256 key generated inside the workload.

## Deployment order

1. Apply the foundation, publish the workload image to its Artifact Registry
   repository, and copy its immutable `@sha256:...` reference. The runtime's
   image-pull role is scoped to this one repository.
2. In `infra/gcp/terraform`, copy `terraform.tfvars.example` to the gitignored
   `terraform.tfvars`, restrict its permissions (`chmod 600 terraform.tfvars`),
   and fill in the image digest, zone ID, and credentials. Terraform loads this
   file automatically. Generate the HMAC secret with `openssl rand -hex 32`
   and use the same value as the web app's `CONFIDENTIAL_SHARED_SECRET`.
   Keep secrets out of the checked-in `production.auto.tfvars`. That file takes
   precedence over `terraform.tfvars`, so change public deployment settings
   there rather than duplicating them in the private file.
3. Set `cloudflare_zone_id` to the existing zone's ID and supply
   `CLOUDFLARE_API_TOKEN` locally with DNS Read/Write permission scoped to that
   zone. Do not commit the token or put it in backend configuration. If the
   hostname already has a DNS record, import it into
   `cloudflare_dns_record.workload` before applying; do not create a duplicate.
   Initialize and review the plan:

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

5. Terraform creates the `A` record from `domain_name` to `public_ip`, with
   Cloudflare proxying disabled. Google's managed certificate remains
   provisioning until DNS resolves to that IP. Inspect backend health and
   certificate status before proceeding.
6. Build and deploy the web app with:

   - `CONFIDENTIAL_SERVER_PUBLIC_URL` from the Terraform output;
   - `CONFIDENTIAL_SHARED_SECRET` matching the Terraform input;
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
- Changing `image_reference` forces replacement of the stateless VM. The
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
