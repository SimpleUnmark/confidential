# Secret Manager: first deployment and rotation

This runbook supersedes all earlier instructions to put credentials in tfvars
or VM metadata. The image currently transferring (`f18c08cc`, digest
`c4b30b69...f7c9`) predates this implementation. It may finish transferring, but
**do not deploy it with this configuration**. A new reviewed image is required.

## Design and boundaries

- Terraform foundation creates the Secret Manager API and two secret containers,
  with user-managed replication in Belgium and Google-managed encryption keys.
- The operator uploads the values directly. Terraform never creates secret
  versions or reads version payloads, so these values never enter new state.
- Runtime grants its service account `secretAccessor` on exactly those two
  secrets. VM metadata holds full, numeric version references, not values.
- The measured Python workload reads the values at startup using a short-lived
  metadata-server service-account token and the HTTPS Secret Manager API.
- Production cannot fall back to old plaintext metadata or credential environment
  variables. Failed access, malformed data, or a checksum failure prevents startup.
- Values remain in Python memory, not files or subprocess-inherited environment.
- Secret Manager access is **IAM-based, not attestation-gated**. Principals able
  to change IAM or reuse/impersonate the identity can obtain credentials. It is
  not a guarantee against a sufficiently privileged infrastructure administrator.
- HPKE payload-decryption keys stay ephemeral inside the workload. They are not
  uploaded to Secret Manager. DeepInfra still receives rewritten text over HTTPS.

## 1. Apply foundation changes (no VM)

If needed, refresh your own CLI authentication with `gcloud auth login`.
Terraform ADC is separate: use `gcloud auth application-default login` only if
Terraform reports an authentication error. The assistant does not run these or
any cloud-changing commands.

```bash
cd /Users/vladimir/Projects/simpleunmark/confidential/infra/gcp/foundation
terraform init
terraform plan
```

If the Belgium registry migration is already applied, expect **3 additions**:
Secret Manager API plus two secret containers. If it is not applied, also expect
the Belgium registry addition and the Frankfurt declarative address move.
Expect **zero deletions**. Stop for unexpected replacements or other changes.

```bash
terraform apply
terraform output secret_resources
```

Both API/secret provisioning and IAM are Terraform-managed. Uploading credential
values is the deliberate exception: secret material must not pass through the
infrastructure state. Do not use a Terraform secret-version data source or put
`secret_data` into a resource.

## 2. Upload the two secret values privately

The upload helper prompts twice without echoing. It sends the value to gcloud on
stdin; it does not save files or place the value in arguments, environment,
Terraform, or shell history. Run it in your own terminal, not through the agent.
Do not paste secrets in this chat. Never run it with terminal tracing enabled.

```bash
cd /Users/vladimir/Projects/simpleunmark/confidential
python3 infra/gcp/upload-secret-version.py deepinfra
python3 infra/gcp/upload-secret-version.py shared
```

For `shared`, use the exact current website backend `CONFIDENTIAL_SHARED_SECRET`
if it has already been securely configured. Otherwise generate a random key of
at least 32 bytes (for example 64 random hexadecimal characters) in your password
manager. Store the same value in the website hosting platform's server-only
secret configuration as `CONFIDENTIAL_SHARED_SECRET`. Never use `NEXT_PUBLIC_*`.
No website Google credentials are needed when using its existing secure secret
configuration; the website HMAC protocol itself is unchanged.

Record the **version numbers** printed by the upload commands (normally `1`
for a new container). Re-running creates another version; it does not overwrite
an existing version. Do not assume `1` after retries.

The uploader needs `secretmanager.versions.add` on these containers. Runtime
access is granted separately by Terraform. Do not solve an upload-permission
error by granting project-wide secret access to the VM.

## 3. Build a new Secret Manager-capable release

Review and commit/push the code changes through the normal repository workflow.
Then run **Release confidential workload** on the reviewed commit/ref in the
public repository. No release/push has been performed automatically by these
local implementation changes.

Wait for CI, the AMD64 Distroless build, and GitHub attestations. Download the
release manifest and record its actual image digest and source commit. Verify
provenance against `SimpleUnmark/confidential` and the expected release workflow.
Do not reuse the old `f18c08cc` digest or tag for this new image.

After replacing `NEW_DIGEST` and `NEW_COMMIT` below with the reviewed release:

```bash
gh attestation verify \
  oci://docker.io/simpleunmark/simpleunmark-confidential@sha256:NEW_DIGEST \
  --repo SimpleUnmark/confidential

gcloud auth configure-docker europe-west1-docker.pkg.dev

docker buildx imagetools create --prefer-index=false \
  --tag europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential:release-NEW_COMMIT \
  docker.io/simpleunmark/simpleunmark-confidential@sha256:NEW_DIGEST

docker buildx imagetools inspect \
  europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential:release-NEW_COMMIT
```

Stop if verification fails or the copied digest differs. The source attestation
remains the provenance authority; copying an image does not necessarily copy its
attestation/referrer artifacts into the destination repository.

## 4. Update checked-in runtime settings

Edit `infra/gcp/terraform/production.auto.tfvars`:

```hcl
image_reference         = "europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@sha256:NEW_DIGEST"
deepinfra_secret_version = "1" # use the actual uploaded version
shared_secret_version   = "1" # use the actual uploaded version
```

These are public configuration values and safe to commit. No private tfvars file
is required. The checked-in image placeholder intentionally fails validation
until the new release digest is supplied. The selected versions must be ENABLED.
Terraform does not read their payloads or confirm credential validity at plan time.

If you made an old private `terraform.tfvars`, remove obsolete credential
assignments yourself. Do not copy them into the public automatic file. If the
old runtime was applied or a plan was saved with real credentials, rotate both
values; old state versions, local files and saved plans do not disappear just
because the configuration changed. Keep historical state protected and review
retention/removal separately; do not destroy the state bucket.

```bash
cd /Users/vladimir/Projects/simpleunmark/confidential/infra/gcp/terraform
terraform init
terraform plan
```

Review the plan: Belgium VM, secret-scoped IAM on exactly two secrets, metadata
containing only version references, no credential-value inputs/outputs. The VM
size remains `n2d-standard-2` unless separately changed after sizing review.

## 5. Deploy and verify

Only when ready to create the VM, review and run `terraform apply`. Set DNS from
the public IP output as described in `terraform/README.md`. Validate TLS/backend
health, `/healthz`, and `/v1/info`. A secret access failure prevents the service
from starting; investigate version state, reference spelling and IAM without
printing secret contents or enabling content logs. Newly granted IAM can take
time to propagate; the launcher restarts failed workloads.

Build/deploy the website with the **new approved image digest** in its browser
allowlist, the unchanged project/service-account policy where applicable, the
workload URL, and the matching server-only HMAC secret. Test attestation, one
Confidential clean, signed usage receipts, credit settlement and failure refunds
using nonsensitive fixtures before real user traffic. Health endpoints alone
do not verify provider credentials or HMAC agreement.

## Rotation

Secrets are fetched once at startup. Adding a Secret Manager version does not
automatically alter a running workload. Upload a new value, update the numeric
pin in the public automatic file, and review/apply Terraform. The version change
replaces the single stateless VM to reload it; expect a brief outage.

DeepInfra-only rotation does not require a website HMAC change. HMAC rotation
requires coordinated backend/workload changes: pause new requests, drain active
work (including receipt retries), update both sides to the same value, deploy,
and test before reopening traffic. This v1 protocol has no dual-key overlap.
Disable old versions only after successful verification and rollback review.

Changing secret values/versions does not change the image digest or ephemeral
payload keys by itself; replacing the workload discards existing in-memory keys.
Never claim this IAM-based storage change provides attestation-bound secret release.
