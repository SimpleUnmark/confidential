# Publishing, approving, and deploying confidential releases

Publishing an image is **not** approving it, and approval is **not** deployment.
The VM and browser never select `latest`, a release tag, or the most recent CI run.

## Trust chain

1. A reviewed commit on protected `main` is built once for `linux/amd64`.
   The release workflow pushes that same local image to Docker Hub and Belgium
   Artifact Registry. It rejects differing digests or an image index instead of
   the single-platform manifest measured by Confidential Space.
2. GitHub signs SLSA-format build provenance for each registry image and the
   browser client tarball using its OIDC identity and public Sigstore/Rekor.
   These are keyless signed attestations: there is no extra long-lived Cosign
   signing key. An additional plain Cosign signature is not needed to verify
   this same evidence. A signature proves provenance, not safe code or a
   reproducible build; this is not a claim of SLSA Build L3.
3. CI uploads a signed `release-candidate.json`, the browser package, and
   verification bundles. The candidate has no authority to change production.
4. An operator reviews the code, provenance and dependencies, then proposes the
   exact digest in `releases/approved-workloads.json` through a PR. CI pins the
   provenance verification to the repository, numeric owner/repository IDs,
   workflow, signer commit, source commit, main ref, GitHub-hosted runners, and
   verified public Rekor inclusion. New entries require both registry attestations.
5. After merging, **Attest approved release policy** waits for approval in the
   separate `release-approval` environment, reverifies image provenance, and
   signs the exact policy file. This is an auditable operator approval record,
   not independent review or multisig in the default solo-maintainer setup.
6. `pnpm release:export-web ../simpleunmark` checks that approval signature,
   checks every usable image's provenance, checks the vendored client hash and
   provenance, then copies the policy into the private website. Review/commit
   that snapshot there. Website builds check the client archive hash and embed
   the allowlist locally; browsers do not fetch an unsigned remote `latest` list.
7. Runtime Terraform derives its immutable image reference from the policy's
   `deploymentDigest` and requires that entry to be `active`. It does not itself
   perform a Sigstore verification: use the verified export/release checks and
   reviewed policy before applying. A Terraform/cloud administrator can bypass
   deployment configuration; an unchanged browser still rejects other digests.
8. For each Confidential request, the browser verifies Google's signed token,
   image digest, workload identity, secure-launch claims and fresh nonce binding
   the request to its ephemeral encryption key. Only then does it send ciphertext.

The initial entry adopts the already deployed `d2470f…` image from `f0b5167…`.
Its Docker Hub provenance was verified on 2026-09-10. Its existing manually
copied GCP image predates GCP attestations; that one exact digest is the only
legacy exception. The website's initial snapshot preserves this existing trust,
not a newly selected image. After merging this setup, sign/export the initial
policy too before deploying the new website build.

## One-time setup (operator runs these)

Use Terraform **1.15.8**. No cloud apply or publication is performed merely by
checking out this code. The state bucket and runtime stay unchanged.

### 1. GCP foundation

```bash
cd infra/gcp/foundation
terraform init
terraform plan
terraform apply
```

Inspect the plan before approving. It adds the GitHub federation pool/provider,
dedicated publisher, repository-scoped writer and public-reader IAM, and the
required identity APIs. It should not replace the VM, secrets or repositories.
The publisher has no Compute, Secret Manager, Terraform-state, or IAM-admin grant.
No GCP JSON key is created or stored in GitHub. WIF admits only the exact public
repository's main release workflow, manual dispatch, `production` environment,
and GitHub-hosted runner; forks, PR jobs, tags and other workflows are excluded.

**Public read applies to every image in the Belgium `workloads` repository.**
It does not make the project, state bucket, runtime secrets or retained Frankfurt
repository public. Keep this registry dedicated to public workloads. If an org
policy prohibits `allUsers`, an explicit policy decision is needed. On 2026-09-11
the operator approved replacing `iam.allowedPolicyMemberDomains` with `allowAll`
**for the entire `simple-unmark-prod` project**. Foundation now manages that
project override and enables the Organization Policy API before creating it.
The organization policy and other projects are unchanged. This is not a
repository-scoped exception: Artifact Registry organization policies do not
support conditions on tags attached directly to repositories. External IAM
grants are now permitted throughout this project, but only the Belgium registry
receives an `allUsers` grant in this configuration. State and secrets retain
their private IAM policies; bucket public-access prevention is unchanged.

The operator needs organization-policy update permissions (typically
`roles/orgpolicy.policyAdmin` on the organization). Terraform does not grant
itself these permissions. Org Policy uses a dedicated Terraform provider with
`user_project_override = true` and `billing_project = var.project_id`, so local
Application Default Credentials charge API quota to `simple-unmark-prod` rather
than the gcloud OAuth client's project. No local ADC edit is necessary. The
operator must have `serviceusage.services.use` on this quota project (included
in `roles/serviceusage.serviceUsageConsumer`). If a 403 names a missing permission,
arrange that specific access; do not broaden IAM roles automatically.
After the policy is created, propagation may briefly cause the same 400 error
on the registry grant. Wait several minutes and rerun plain `terraform plan`
and `terraform apply` in foundation; do not taint/recreate existing resources.
To roll back, remove public/external IAM grants first, then remove the project
override to restore inheritance. Restoring the restriction alone does not
revoke previously granted access.
Public downloads incur registry storage/egress costs; monitor quotas and billing.

### 2. GitHub configuration

From the repository root:

```bash
export GITHUB_TOKEN="$(gh auth token)"
cd infra/github
terraform init
terraform plan
terraform apply
unset GITHUB_TOKEN
```

The token needs repository administration, environment and variable-management
access. It is a local Terraform operator credential, **not** a new CI secret;
never put it in tfvars. This stack reads foundation outputs from private GCS
state and sets four non-secret `GCP_*` environment variables automatically.
It adopts existing `production`, retains `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`,
adds the separate approval environment, and protects main against direct pushes,
force pushes and deletion, requiring up-to-date CI and resolved conversations.

The default release reviewer is the existing owner `haltakov` (ID 300777).
Only that account currently has write access, so a second-person review is not
claimed: PRs/checks are required, but self-publication approval is allowed and
the PR approval count is zero. After adding a second maintainer, set
`independent_review_required = true` and appropriate `release_reviewer_ids` in
a local `*.auto.tfvars` (non-secret settings). This requires another reviewer
for PRs and prevents self-approval of environment jobs. Do not enable it while
solo, or you will block your own releases. A repo admin can change protections;
this setup does not defend against every action of the GitHub/GCP owner.

Apply these protections **before enabling/merging the new release workflows**.
Allow a few minutes for GCP IAM propagation. Keep the existing Docker Hub token
scoped as narrowly as Docker Hub supports and rotate it separately.

### 3. First automatic GCP publication

Merge the reviewed code, then run **Release confidential workload** from the
GitHub Actions UI, selecting `main`. Approve its `production` environment job.
Tag pushes no longer publish; an unreviewed branch/tag cannot mint a GCP token.
Unique `release-COMMIT-RUN-ATTEMPT` tags are discovery aids, not trust anchors.
No manual `gcloud auth configure-docker` or cross-registry copy is needed.

A partial push or failed attestation is a **failed candidate**, never an approved
release. Retry after fixing the error. Do not approve an image from a failed run.
Both registry digests and all evidence must be present. Existing deployed images
remain trusted and running regardless of publication success.

Verify public GCP read without cached Docker credentials, using the resulting
digest (this is read-only):

```bash
registry_check_dir=$(mktemp -d)
DOCKER_CONFIG="$registry_check_dir" docker manifest inspect \
  europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@sha256:DIGEST
```

## Approving a candidate

Download its workflow artifact. Verify the candidate file itself, substituting
the actual reviewed source commit in BOTH digest flags:

```bash
gh attestation verify release-candidate.json \
  --repo SimpleUnmark/confidential \
  --signer-workflow SimpleUnmark/confidential/.github/workflows/release-confidential.yml \
  --source-ref refs/heads/main --source-digest COMMIT --signer-digest COMMIT \
  --deny-self-hosted-runners
```

Check the run succeeded and manually review the claimed commit/code. Copy the
candidate's digest/source/provenance fields into a new policy workload entry,
choose `active`, and record `approvedAt` as the UTC approval date. **Do not** copy
`status: candidate` or infer approval from a file's name. Leave `deploymentDigest`
at the old image initially. Update `client` only if intentionally upgrading the
vendored package; otherwise retain the already-reviewed compatible client hash.
Protocol 4 is required by the current website. Test changes to wire behavior.

```bash
pnpm release:validate
pnpm release:verify
```

Open/merge the policy PR only after verification and review. Approve the
`release-approval` job, then pull the merged main and run:

```bash
pnpm release:export-web ../simpleunmark
```

This fails closed if the policy is uncommitted, lacks its approval attestation,
has altered evidence, or the installed browser package does not match. By default
it verifies the last commit modifying the policy. If you manually re-attested
the same file at a newer main commit, pass that exact SHA as the final argument:
`pnpm release:export-web ../simpleunmark APPROVAL_COMMIT`.

Archive the candidate, policy, tarball and `.sigstore.json` bundles outside the
90-day Actions artifact retention window if long-term offline auditing matters.
Transparency records are evidence, not permanent storage of image layers or
tarballs. No blockchain is needed. Keep both registries as useful public mirrors;
production pulls only from GCP and never falls back to Docker Hub automatically.

## Rollout, rollback and revocation

1. Approve old + new images; export the signed policy and deploy the website
   accepting both. Remove the legacy `NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_IMAGE_DIGESTS`
   Coolify build variable, or it must exactly equal the reviewed list. Identity,
   hardware, audience and workload-origin variables are unchanged.
2. In another reviewed policy PR, change `deploymentDigest` to the new active
   digest. Sign/export that policy; from `infra/gcp/terraform`, run plain
   `terraform init`, `terraform plan`, then `terraform apply` yourself. The
   single stateless VM is replaced: drain work and expect a brief outage.
3. Check `/healthz`, `/v1/info`, then a disposable **Confidential text clean**.
   Verify the actual attested digest, encrypted browser-to-workload traffic,
   encrypted response and content-free accounting/credit settlement. Health
   endpoints alone are not a security test. Also check Private mode still works.
4. Mark the old digest `retiring` during the overlap, then `revoked` with a
   `reason` when no longer accepted. Keep its entry as an audit tombstone.
   Sign/export and rebuild the website again. CI rejects history deletion,
   altered historical evidence and reactivation of a revoked image.

For rollback, choose an **active, non-revoked** known-good digest, export/deploy
the browser policy first if needed, and apply runtime Terraform. A `retiring`
image may be made active through review; a revoked digest must not be restored.

For an incident, revoke the bad digest and choose a known-good active target,
sign/export/rebuild urgently and replace or stop the bad VM. Already-open tabs
and cached old JS retain their previous policy: a new manifest cannot instantly
revoke those clients. If no image is safe, disable Confidential authorization/
take the service offline instead of keeping a compromised image alive to satisfy
the normal rollout order. This is operator incident response, not automatic CI
deployment. Do not delete evidence to simulate revocation.

## Remaining limits

- The website operator still delivers the browser JavaScript. An independently
  distributed/pinned client is needed to remove this trust, not another registry.
- Approved code, dependencies, GitHub's builder, Google attestation and hardware
  remain trusted. SHA-pinned actions are updated through Dependabot PRs; pinned
  actions do not make all transitive build inputs reproducible.
- DeepInfra still receives rewritten text in plaintext over HTTPS. Confidential
  GPU inference is not deployed. This work does not change cleaning/model behavior.
- Secret Manager's VM credentials are IAM-controlled, not attestation-released.
  The CI federation pool is **only for publishing**, not a new runtime dependency.
- Repository-scoped writer permissions can publish/tag artifacts, but cannot
  change the browser's embedded digest policy. GCP/Docker Hub outages may block
  verification or publication; verification fails closed, never accepts a tag.

References: [Google deployment federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines),
[Artifact Registry access control](https://docs.cloud.google.com/artifact-registry/docs/access-control),
[GitHub OIDC claims](https://docs.github.com/en/actions/reference/security/oidc),
[GitHub attestation verifier](https://cli.github.com/manual/gh_attestation_verify).
