# Confidential architecture

This document describes the complete confidential chain end to end: what happens
while a user cleans content, how a new workload version reaches production, and
which safeguard stands where. It is the spine that connects the other documents;
each section links to the deeper reference rather than restating it.

- Protocol and endpoint reference: [confidential service](../apps/confidential-server/README.md)
- Browser verification contract: [client](../packages/client/README.md)
- Release trust chain and operator runbook: [release-security.md](release-security.md)
- Step-by-step rollout gates: [deployment-checklist.md](deployment-checklist.md)
- Cloud topology: [infra/gcp](../infra/gcp/README.md), [runtime Terraform](../infra/gcp/terraform/README.md)

Two claims frame everything below. In Confidential mode, submitted content is
readable only inside an attested workload image that the browser checked before
encrypting. And no single automated step promotes code to production: publishing,
approving, exporting, and deploying are four separate, separately gated actions.
Both claims have explicit limits, collected in [Limits](#limits-that-survive-the-whole-chain).

## Participants and trust domains

| Participant | Role | Trusted with |
| --- | --- | --- |
| Browser JavaScript | Measures text, verifies attestation, encrypts, decrypts | Plaintext. Served by the website, so not independent of it. |
| Website (private repo) | Authenticates users, mints capabilities, records receipts, serves JS | Identity, billing, metadata. **Not** Confidential-mode content. |
| Confidential workload | Decrypts, cleans, re-encrypts | Plaintext, for the life of one request. |
| Google Confidential Space | Measures the image, signs attestation tokens | Hardware/firmware root of trust. |
| Secret Manager | Holds the DeepInfra key and shared HMAC key | Two runtime credentials. Neither decrypts HPKE payloads. |
| DeepInfra | Rewrites text | Receives rewritten text in plaintext over HTTPS. Outside the HPKE boundary. |
| GitHub Actions | Builds, publishes, signs provenance | Build integrity. Cannot change what the browser accepts. |
| Operator | Reviews, approves, applies | Everything. Deliberately the only path to production. |

The shared HMAC secret is the only thing the website and workload share. It
authorizes and accounts; it never participates in content encryption.

## Cleaning a request

### 1. Authorization (website)

The browser measures the input locally — `countWords`, `countCharacters`,
`countInputBytes` in `packages/client/src/measurements.ts` — and asks the website
to authorize a clean. The website checks the account, reserves credits, and
returns a request ID plus an HMAC-signed capability
(`CleanAuthorizationSuccess` in `packages/client/src/clean-protocol.ts`).

The capability is a signed statement of what may be cleaned, not a session token.
`apps/confidential-server/simpleunmark_confidential/security.py` requires that it
carry `aud = simpleunmark-confidential-server`, a request ID, issue and expiry
times at most ten minutes apart, the calling origin, the mode
(`PRIVATE` or `CONFIDENTIAL`), the asset kind, and the exact measurements. Media
capabilities additionally pin extension, MIME type, operation, and the exact
original byte count, and must be v2 Confidential. Text capabilities must carry no
file metadata. A Private capability is rejected by the Confidential endpoints and
vice versa, so a mode cannot be downgraded after issuance.

The measurements are load-bearing twice: the workload refuses content that does
not match them, and the website bills against the same numbers. Substituting a
larger file or different operation under an existing authorization fails
signature-independent validation.

### 2. Attestation handshake

The browser generates a 32-byte random challenge and calls `POST /v1/attestation`
with the capability as a bearer token. The workload checks the signature, the
`CONFIDENTIAL` mode, and that the `Origin` header equals both the capability's
origin and a compiled-in allowed origin.

It then mints a fresh HPKE key pair for this request —
`WorkloadEncryptionKey` in `crypto.py`, `DHKEM(P-256, HKDF-SHA256) /
HKDF-SHA256 / AES-128-GCM`. The private key exists only in process memory and is
never written anywhere. It computes the binding

```
nonce = base64url(SHA-256("simpleunmark-attestation-v2\n" + requestId + "\n" + challenge + "\n" + publicKey))
```

and asks the Confidential Space launcher, over its local Unix socket
(`attestation.py`), for an OIDC token carrying that nonce. The response returns
the Google-signed token, the workload public key, and its fingerprint.

Binding the public key into the nonce is what makes the handshake meaningful: a
proxy cannot present a Google-signed token for the real workload alongside a key
it controls, because the token commits to the key.

Concurrency and retry behaviour matter here because they are security-relevant
state, not just performance. Entries are cached per request ID until the
capability expires; identical concurrent retries share one launcher token request
(`mint_attestation`); a retry with a *different* challenge for an already-attested
request ID is refused with 409; the cache is capped at 2,000 entries; and both the
key cache and the token pacing are process-local. That last fact is why v1 must
stay a single instance — see the backend comment in
`infra/gcp/terraform/main.tf`.

### 3. Browser-side verification

`verifyConfidentialSpaceToken` in
`packages/client/src/confidential-attestation.ts` verifies the JWT's RS256
signature against Google's published JWKS, with issuer
`https://confidentialcomputing.googleapis.com` and the configured audience. Then
`validateConfidentialSpaceClaims` checks, against a policy supplied by trusted
application configuration and never learned from the workload:

- `swname = CONFIDENTIAL_SPACE`, `dbgstat = disabled-since-boot`, `secboot = true`
- `support_attributes` includes `STABLE` (production image, not debug)
- `monitoring_enabled.memory = false`
- hardware model, GCP project number, and service account all on the allowlist
- `container.image_digest` in the approved digest set
- the `eat_nonce` matches the binding computed in step 2
- no command or environment override (absent or empty accepted; null, malformed,
  or non-empty rejected — see the client README for why absence is accepted)

An empty allowlist fails closed. `validateConfidentialSpaceClaims` alone is for
tests; production must verify the signature. Only after all of this does any
plaintext get encrypted.

### 4. Encrypted request

The browser encrypts under the attested public key in HPKE base mode, with
`info = simpleunmark-hpke-v1` and additional authenticated data
`simpleunmark-request-v1\n{requestId}`. From the same HPKE context both sides
export a 32-byte AES-GCM response key and 12-byte base nonce, under labels
`simpleunmark-response-key-v1` and `simpleunmark-response-nonce-v1`. The response
key is therefore never transmitted.

Text goes to `POST /v1/clean` as JSON (`{v, enc, ciphertext}`, base64url). Media
goes to `POST /v1/media/clean` as a binary frame that avoids base64 expansion:

```
outer:      SUME1 || uint16(enc length) || HPKE enc || ciphertext
plaintext:  SUMM1 || uint32(header length) || JSON header || file bytes
response:   SUMR1 || uint32(header length) || JSON result || file bytes
```

### 5. Workload-side checks, in order

Every guard runs before any cost is incurred:

1. Capability signature, expiry, mode, and asset kind.
2. Origin header against the capability and the allowlist.
3. Body size and a read timeout (15 s general, 120 s media), enforced while
   streaming so one oversized ASGI chunk cannot be buffered twice (`_read_body`).
4. A cached attestation entry must exist for this request ID, or the request is
   refused with `428` — cleaning without a completed handshake is impossible.
5. At most five decryption attempts per attestation; the sixth drops the entry
   and forces re-attestation. This bounds oracle-style retries while still
   letting a genuinely corrupted upload be retried.
6. HPKE decryption, then schema validation of the plaintext.
7. Measurements must match the capability exactly: word, character, and byte
   counts for text; asset kind, extension, MIME type, operation, and byte count
   for media. A mismatch is `403`, not a silent reprice.
8. Media only: if a job is already running, `429 BUSY` — rejected rather than
   queued in enclave RAM, and rejected *before* the reservation is claimed, so
   the browser can abandon and retry at no cost.
9. The signed, metadata-only `started` receipt atomically claims the reservation.
   Only then is the attestation entry dropped and the ephemeral key released.

### 6. Processing

Text (`text.py`) runs the pinned upstream `watermarks-remover` v0.7.0
deterministic pass in conservative mode — normalizing exotic spaces and removing
unsupported invisible carriers while preserving load-bearing emoji glue, script
joiners, variation selectors, subdivision flags, and bidi controls — then one
DeepInfra paraphrase. **The paraphrase leaves the confidential boundary:**
DeepInfra receives and returns plaintext over its normal authenticated HTTPS API.

Media (`media.py`) routes to the upstream image or A/V metadata cleaner inside a
per-request temporary directory that is deleted before the response is returned.
In production that directory is Confidential Space tmpfs
(`tee-mount=type=tmpfs,...,destination=/tmp/simpleunmark`), so scratch bytes stay
in protected VM memory rather than the boot disk. The opt-in destructive audio
operation additionally applies tempo, pitch, EQ, and AAC re-encoding, then strips
metadata again. Media work runs under `run_media_job`, which shields the worker
thread so a disconnecting client cannot release the single-job lock while FFmpeg
is still running.

### 7. Encrypted response and accounting

Text streams newline-delimited envelopes `{v, sequence, ciphertext}`. Each event
is AES-GCM encrypted with a sequence-derived nonce and AAD
`simpleunmark-response-v1\n{requestId}\n{sequence}`, and the browser's
`ResponseDecryptor` requires the exact next sequence number. Reordered,
duplicated, replayed, or cross-request events fail authentication rather than
being tolerated. Media returns one encrypted payload at sequence 0.

In parallel, the workload posts HMAC-signed receipts to the website:
`started`, then `succeeded` or `failed` with an error code. A receipt carries
counts, mode, asset kind, operation, status, request ID, and token/cost/timing
telemetry — no input, output, prompt, filename, removed metadata value, or
content fragment (`_receipt_base` and `_telemetry` in `app.py`). Failure and
client-abort paths submit a `failed` receipt so credits are returned; the abort
path shields that submission through cancellation. A failed rewrite where the
deterministic pass did change something still returns the deterministic result,
with a warning and zero credits charged.

### 8. Private mode

`POST /v1/plain/clean` is the deliberately simpler boundary: the website proxies
plaintext, and the same capability, origin, measurement, receipt, and reservation
logic applies with no attestation or encryption. It exists so the two modes share
one accounting path. Neither mode stores submitted or cleaned text in the
application database.

## Runtime environment

The VM has no public IP; ingress reaches port 8080 only from Google's load
balancer ranges (`35.191.0.0/16`, `130.211.0.0/22`), egress goes through Cloud
NAT, and the external HTTPS load balancer terminates TLS with a 420-second
timeout sized for a large upload plus one bounded FFmpeg pass. Serial port and
project SSH keys are disabled; OS Login is on. The instance runs SEV confidential
compute on AMD Milan with Secure Boot, vTPM, and integrity monitoring.

The image is Distroless/nonroot, and its launch policy labels deny capabilities,
cgroups, command override, environment override, log redirection, and memory
monitoring, permitting exactly one mount destination. The browser independently
rejects tokens that report overrides or memory monitoring, so the policy is
enforced twice.

Production configuration is baked into the image, not supplied by metadata: the
receipt URL, allowed origin, DeepInfra endpoint and model, and attestation
audience are all `ENV` in the Dockerfile. Changing any of them requires a new
image and a new digest — which means a new browser approval — rather than an
environment override on a running VM.

At boot (`bootstrap.py`), production reads two *numeric* Secret Manager version
references from instance metadata, obtains a short-lived token from the attached
service account, and fetches both payloads over HTTPS with `trust_env=False` and
redirects disabled, so a metadata-supplied URL can never receive the access
token. Payloads are CRC32C-verified, format-checked, and held in memory only —
never written to disk or into an environment FFmpeg would inherit. Any failure
aborts startup; there is no fallback to plaintext metadata or environment
credentials. This is IAM-gated secret access, **not** attestation-gated release:
an administrator who can change IAM or impersonate the workload identity can read
these secrets. Neither secret decrypts recorded HPKE traffic.

`GET /healthz` and `GET /v1/info` expose liveness and version information only.
`/v1/info` is explicitly informational — the code says so — and must never be
used as a trust root; the attestation token is.

## Releasing a new version

Publication is not approval, and approval is not deployment. Neither the VM nor
the browser ever resolves `latest`, a release tag, or "the most recent CI run".

**1. Build and publish.** *Release confidential workload* is manual-dispatch only,
requires `refs/heads/main`, and runs the full CI suite first. It builds one
`linux/amd64` image, then pushes that same local image to Docker Hub and Belgium
Artifact Registry, verifying afterwards that both digests are identical and that
each is a single-platform manifest — an image index would not be what Confidential
Space measures. GCP access uses short-lived GitHub OIDC federation scoped to this
repository, this workflow, manual dispatch, the `production` environment, and
GitHub-hosted runners; there is no stored GCP key, and the publisher identity has
no Compute, Secret Manager, state, or IAM-admin grant.

**2. Sign evidence.** GitHub signs SLSA-format provenance via keyless Sigstore for
both registry images, the browser client tarball, and an unapproved
`release-candidate.json`. A signature proves who built what from which commit. It
does not prove the code is safe, and this is not a claim of SLSA Build L3.

**3. Operator review and policy PR.** A human reviews the code, dependencies, and
provenance, then proposes the digest in `releases/approved-workloads.json`.
`scripts/release-policy.mjs` pins verification to the repository, its *numeric*
owner and repository IDs, the signer workflow, signer and source commit, the main
ref, non-self-hosted runners, and verified public Rekor inclusion. New entries
require attestations from both registries (one legacy digest is the single,
explicitly recorded exception). `validateTransition` refuses to let history be
deleted, historical evidence be rewritten, or a revoked image be reactivated.

**4. Attested approval.** Merging to main triggers *Attest approved release
policy*, which waits in the separate `release-approval` environment, re-runs
verification, and signs the exact policy file. This is an auditable approval
record. With a single maintainer it is not independent review or multisig, and
the repo says so; `independent_review_required` exists for when a second
maintainer is added.

**5. Export to the browser.** `pnpm release:export-web ../simpleunmark` fails
closed unless the policy is committed, carries its approval attestation at that
exact commit, verifies every usable image's provenance, and matches the vendored
client tarball by hash and provenance. Only then does it copy the policy into the
website, which embeds the allowlist at build time. Browsers never fetch a remote
unsigned list.

**6. Deploy.** Runtime Terraform derives the image reference from the policy's
`deploymentDigest` and has a `precondition` requiring that digest to be `active`
in the reviewed policy. The operator runs `plan` and `apply` — CI never deploys.
Terraform does not itself perform Sigstore verification; the export and release
checks do. A cloud administrator can bypass deployment configuration, but an
unchanged browser still rejects any digest outside its embedded allowlist.

Rollout order matters: approve old and new digests and ship the browser policy
that accepts both *before* switching `deploymentDigest`. The VM name is coupled to
a `terraform_data` generation ID derived from the image reference and secret
versions, so an image or secret change produces a genuinely new instance and an
explicit instance-group membership diff — see
[instance-group-replacement.md](instance-group-replacement.md) for why the
same-name replacement failed. This is a single stateless VM: expect a brief
outage. Afterwards mark the old digest `retiring`, then `revoked` with a reason,
keeping the entry as an audit tombstone.

## Where each safeguard lives

| Safeguard | Enforced in | Stops |
| --- | --- | --- |
| Capability HMAC, expiry, mode, origin | Workload `security.py` | Unauthorized, expired, cross-mode, or cross-origin cleaning |
| Measurement binding | Workload, against browser-computed values | Under-billing and operation/type substitution |
| Nonce binds key to token | `attestation_binding`, both sides | A proxy substituting its own encryption key |
| Full JWT signature + claim checks | Browser `confidential-attestation.ts` | Unapproved image, project, SA, hardware, debug state |
| Local policy, never `/v1/info` | Browser, from build-time config | The workload vouching for itself |
| Per-request HPKE key, memory only | `crypto.py` | Retroactive decryption of recorded traffic |
| Sequenced AEAD responses | Both sides | Replay, reordering, duplication, cross-request splicing |
| `428` without attestation; 5-attempt cap | `app.py` | Cleaning without a handshake; decryption retry abuse |
| Metadata-only receipts | `_receipt_base` / `_telemetry` | Content reaching the database, logs, or notifications |
| Reservation claimed before spend; `429` before claim | `app.py` | Double-spend and charging for rejected work |
| Baked-in production config | Dockerfile `ENV` | Silent endpoint/model/audience changes without a new digest |
| Launch policy + attested override claims | Image labels *and* browser | Command/env override, log redirect, memory monitoring |
| In-memory, CRC-checked secret bootstrap | `bootstrap.py` | Credentials on disk, in env, or inherited by FFmpeg |
| Dual-registry digest equality, single-platform check | Release workflow | Registry divergence; an index instead of a measured manifest |
| Pinned provenance verification | `release-policy.mjs` | Forged or foreign-built artifacts; self-hosted runners |
| Transition rules | `validateTransition` | History deletion, evidence rewriting, un-revocation |
| Separate approval environment | `approve-release-policy.yml` | Publication implying approval |
| Active-digest precondition | Runtime Terraform | Deploying an unapproved or revoked image |
| Overlapping-allowlist rollout | Operator procedure | Locking out in-flight browsers during rotation |

## Limits that survive the whole chain

None of the above changes these. They are stated here so the diagram is not read
as more than it is; the [threat boundary](../apps/confidential-server/README.md#threat-boundary)
carries the full list.

- The website serves the JavaScript that does the verifying. A compromised
  website can exfiltrate text before encryption. Browser verification removes
  trust in the web backend's *attestation decision*, not in its code delivery. An
  independently distributed, pinned client is the stronger follow-up.
- DeepInfra receives rewritten text in plaintext. There is no confidential GPU
  inference in this stack, and Confidential AI is a planned mode, not a shipped
  guarantee.
- Secret Manager access is IAM-based. Sufficiently privileged administrators can
  obtain both runtime credentials.
- Request counts, timing, ciphertext sizes, media category, operation, extension,
  and exact byte size remain observable to the authorization layer and network.
- Media cleaning removes container metadata and provenance. It does not remove
  visible overlays or signals in pixels, frames, or waveforms. The destructive
  audio pass may disrupt waveform signals but guarantees nothing against any
  detector, and no vendor detector ships here. Removing orientation and colour
  profile can change how the same bytes display.
- A TEE reduces infrastructure access; bugs or deliberate exfiltration inside an
  approved image remain trusted. Review what you approve.
- Already-open tabs keep the policy they loaded. Revocation does not reach them
  instantly; incident response may require taking the service offline.

## Changing the protocol

`protocolVersion` 4 appears in `/v1/info`, the release policy, the policy
validator, and the runtime Terraform precondition, and the website checks it too.
A wire change therefore touches, together: the workload endpoint and `crypto.py`
labels, `packages/client/src/confidential-crypto.ts`, the shared
`fixtures/deterministic-cleaning.json` if cleaning behaviour moves, the version
constant in all four places above, and the website's expectations. Ship a new
image and approve it before the website requires the new version, using the same
overlapping rollout as any other rotation.
