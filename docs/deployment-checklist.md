# Confidential service deployment checklist

## Release gates

1. Review the Python workload, public client, launch policy, and dependency pins.
   Run `pnpm lint`, `pnpm typecheck`, and `pnpm test`. CI also builds the AMD64
   Distroless image and validates Terraform without backend access.
2. Configure this public repository's `production` Docker Hub variable/secret
   as described in [migration notes](repository-migration.md).
3. Run the release workflow on reviewed source. Download `release-manifest.json`
   and the client tarball from its artifact. Record the public commit and image
   digest. CI success alone is not release attestation verification.
4. Verify the exact Docker Hub image and client tarball:

   ```bash
   gh attestation verify \
     oci://docker.io/simpleunmark/simpleunmark-confidential@sha256:DIGEST \
     --repo SimpleUnmark/confidential
   gh attestation verify simpleunmark-confidential-client-0.1.0.tgz \
     --repo SimpleUnmark/confidential
   ```

   Inspect provenance for the expected commit and
   `.github/workflows/release-confidential.yml`. Merely accepting any attestation
   from the repository is not a substitute for approving a specific release.
5. Compare the tarball's SHA-256 with `clientSha256` in the manifest. Copy the
   OCI image to Artifact Registry without rebuilding and require the destination
   digest to match. See the [GCP guide](../infra/gcp/README.md).

## Infrastructure and integration

1. Review/apply foundation changes first: APIs, registries, and Secret Manager
   containers. Retain the exact backend bucket and state prefixes.
2. Follow [Secret Manager setup](../infra/gcp/secret-manager-migration.md). Upload
   the DeepInfra and shared HMAC values directly, outside Terraform. Set the NEW
   image digest and numeric secret versions in checked-in `production.auto.tfvars`.
   No private tfvars or DNS credentials are needed. Never deploy the previous
   plaintext-metadata image with the new bootstrap configuration.
3. From `infra/gcp/terraform`, run `terraform init`, `terraform plan`, and after
   review `terraform apply`. The operator runs these commands, not the assistant.
4. Run `terraform output` and manually point `confidential.simpleunmark.com`
   to `public_ip` with a DNS-only A record. Wait for HTTPS certificate and
   backend health. Verify `/healthz` and
   `/v1/info`. These can be tested independently but are not proofs of attestation.
5. Configure the authorization/receipt backend with the same HMAC key and the
   workload URL. The production image fixes the receipt URL, CORS origin,
   DeepInfra destination/model, and attestation audience; changing them requires
   a new image, not an environment override.
6. Build the website/client with the exact image digest, GCP project number,
   service account, `GCP_AMD_SEV`, workload origin, and audience. Use the
   Terraform outputs and reviewed image manifest, never `/v1/info` as a trust root.

## End-to-end acceptance

Health checks are not enough. With a compatible backend, check attestation and
HPKE decryption, a full text clean, encrypted media, forged/unapproved digests,
duplicate/reordered responses, metadata-only signed receipts, credit settlement,
failure refunds, and the concurrent-media `429 BUSY` path. Accounting is owned
and tested by the private application; see its deployment checklist too.

DeepInfra still receives rewritten text in plaintext over HTTPS. No confidential
GPU inference or visible-overlay removal is deployed by this stack. Never test
with sensitive real-user content before the full acceptance checks pass.

For later image rotations, update the browser policy to accept old and new
approved digests first, replace the single stateless VM, test, then remove the
old digest from the next web build. v1 may have a brief outage during replacement.
