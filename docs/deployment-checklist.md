# Confidential service deployment checklist

## Release gates

1. Review the Python workload, public client, launch policy, and dependency pins.
   Run `pnpm lint`, `pnpm typecheck`, and `pnpm test`. CI also builds the AMD64
   Distroless image and validates Terraform without backend access.
2. Follow [release-security.md](release-security.md) to configure GCP publishing
   and GitHub approval protections through Terraform (operator applies).
3. Run **Release confidential workload** on reviewed main. CI builds once,
   publishes identical digests to both registries and signs provenance. Download
   the signed `release-candidate.json`, tarball and verification bundles.
4. Verify candidate provenance against the expected workflow AND source/signer
   commit. Review the code and both registry attestations; add the digest through
   a policy PR. `pnpm release:verify` must pass. CI success is not human approval.
5. Merge/sign the policy through **Attest approved release policy** and run
   `pnpm release:export-web ../simpleunmark`. This verifies the policy signature,
   image provenance and compatible vendored client hash/provenance before copying.
   Deploy the website's overlapping allowlist before rotating an existing VM.

## Infrastructure and integration

1. Review/apply foundation changes first: APIs, registries, and Secret Manager
   containers. Retain the exact backend bucket and state prefixes.
2. Follow [Secret Manager setup](../infra/gcp/secret-manager-migration.md). Upload
   the DeepInfra and shared HMAC values directly, outside Terraform. Select the
   active deployment digest in the reviewed release policy and numeric secret
   versions in checked-in `production.auto.tfvars`.
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
