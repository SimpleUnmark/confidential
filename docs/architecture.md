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

Two claims frame everything below, and both are narrower than they first sound.

**Confidentiality.** In Confidential mode the browser encrypts to a key that
exists only inside an attested workload image, and it verifies that image before
encrypting. For media that is the whole story: the file never leaves the enclave.
For text it is not — the paraphrase step sends the text to DeepInfra in plaintext.
Read [The confidentiality boundary](#the-confidentiality-boundary) before
describing this mode to anyone.

**Release control.** No single automated step promotes code to production:
publishing, approving, exporting, and deploying are four separate, separately
gated actions. They are four separate *steps*, not four separate *people* — see
[Role separation today](#role-separation-today).

Both claims have further limits, collected in
[Limits](#limits-that-survive-the-whole-chain).

## Participants and trust domains

| Participant | Role | Trusted with |
| --- | --- | --- |
| Browser JavaScript | Measures text, verifies attestation, encrypts, decrypts | Plaintext. Served by the website, so not independent of it. |
| Website (private repo) | Authenticates users, mints capabilities, records receipts, serves JS | Identity, billing, metadata. **Not** Confidential-mode content. |
| Confidential workload | Decrypts, cleans, re-encrypts | Plaintext, for the life of one request. |
| Google Confidential Space | Measures the image, signs attestation tokens | Hardware/firmware root of trust. |
| Secret Manager | Holds the DeepInfra key and shared HMAC key | Two runtime credentials. Neither decrypts HPKE payloads. |
| DeepInfra | Rewrites text | The submitted text itself, in plaintext over HTTPS, after the local deterministic pass. Outside the HPKE boundary; protected by provider policy only. |
| GitHub Actions | Builds, publishes, signs provenance | Build integrity. Cannot change what the browser accepts. |
| Operator | Reviews, approves, applies | Everything. Deliberately the only path to production — and today one person filling all four roles. |

The shared HMAC secret is the only thing the website and workload share. It
authorizes and accounts; it never participates in content encryption.

## The confidentiality boundary

The HPKE boundary is not the same shape for text and for media, and that
difference is the most important thing to understand about this system.

**Media never leaves the enclave.** `media.py` makes no outbound content call at
all. The file is decrypted in memory, cleaned inside a per-request temporary
directory, re-encrypted, and the directory is deleted before the response is
returned. Only metadata receipts leave the VM.

**Text does leave the enclave.** The text pipeline has two stages
(`_processing_stream` in `app.py`):

1. A deterministic local pass (`clean_deterministically`) that normalizes exotic
   spaces and removes unsupported invisible carriers. This runs entirely inside
   the enclave and changes almost nothing a reader would notice.
2. One DeepInfra paraphrase, which receives the *output of stage 1* — the user's
   submitted text, minus invisible characters, with spaces normalized. Nothing is
   redacted, truncated, summarized, chunked, or otherwise reduced first.

For any practical purpose, therefore: **DeepInfra receives the source text.** It
travels over DeepInfra's ordinary authenticated HTTPS chat-completions API
(`provider.py`) — TLS to `api.deepinfra.com`, a bearer API key loaded from Secret
Manager, and the text as the `user` message of a streaming request, with the
rewrite instructions as the `system` message. It is TLS in transit and plaintext
at the application layer: DeepInfra's servers see it in the clear, because they
have to in order to run the model on it.

HPKE does not extend across this hop. The attestation the browser verified says
nothing about it. No key the browser checked protects it. A user who trusts
everything up to and including the enclave must still trust DeepInfra separately
for the rewriting of text.

### What DeepInfra commits to

These are DeepInfra's own published statements, quoted so the difference between
them and the rest of this document stays visible.

From its [data-privacy documentation](https://docs.deepinfra.com/account/data-privacy):

- "Input data is not stored to disk during inference — it exists only in memory
  while the request is being processed." When inference completes "the data is
  deleted from memory", and outputs are "sent to you and then deleted."
- "We generally do not log the content of your requests. We log metadata useful
  for debugging: request ID, cost, sampling parameters."
- **The retention exception, stated in the same document:** "We reserve the right
  to log a small portion of requests when necessary for debugging or security
  purposes." Note what this is: an unannounced sample of request *content*, of
  unspecified size, at DeepInfra's discretion. The documentation describes no
  notice, no opt-out for the standard API, and no signal in the response. Neither
  this workload nor the browser can tell whether a given clean was sampled.
- A second exception covers bulk inference APIs, where data "may need to be
  stored for a longer period, potentially on disk in encrypted form" and is
  deleted "after a short retention period". This stack does not use those APIs —
  `provider.py` calls the streaming `/chat/completions` endpoint — so it does not
  apply today. It would apply if the call shape ever changed, which is a reason
  to treat the provider call shape as a security-relevant decision.

From its [privacy policy](https://deepinfra.com/privacy): "We will not store,
sell, or train using this data unless we have your explicit consent." Account
data is removed 30 days after account deletion, with the usual carve-outs for
legal process, billing, and collection.

### Provider policy is not cryptographic protection

Everything in the section above is a **provider policy commitment**. It is
enforced by DeepInfra's own operational practice and terms, and it carries its
own stated exceptions. That is a categorically different kind of claim from the
rest of this chain:

| | Enforced by | Verifiable by the browser | How it fails |
| --- | --- | --- | --- |
| Enclave confidentiality | AMD SEV, Confidential Space measurement, HPKE | Yes — every request, before any plaintext is encrypted | Closed. The browser refuses to encrypt and the user sees an error |
| DeepInfra confidentiality | Provider policy and contract | No | Silently. Nothing observable changes at any layer |

A misconfiguration, an insider, a breach, a legal demand, or a routine debug
sample at DeepInfra is not something the attestation chain detects, prevents, or
even notices. So Confidential mode should never be described to a user as "your
text is never readable outside the enclave". The accurate statement is narrower:
the text is readable by DeepInfra, under DeepInfra's published policy, for the
duration of the rewrite, and by nobody else outside the enclave.

### Confidential GPU inference: planned, deferred for cost

The intended end state is self-hosted inference on an attested confidential GPU —
an H100/Blackwell-class device in confidential-compute mode, with the model
weights and the paraphrase running inside the same measured boundary the browser
already verifies, and the GPU's own attestation folded into the evidence the
browser checks. That would move the text hop from a provider policy commitment to
the same cryptographic and hardware-rooted guarantee as the rest of the request,
and it would delete this entire section.

It is deferred because of cost, not because of design or feasibility. Serverless
per-token inference is paid only when someone actually cleans text; a dedicated
confidential GPU instance is paid continuously whether or not anyone does, and it
also requires hosting, updating and securing the model. At current volume that
standing cost is larger than the product supports. This is a deliberate and
revisitable trade-off, and it is precisely why Confidential AI is described
throughout this repository as a planned mode rather than a shipped guarantee.
Until it ships, the text boundary is exactly where this section puts it.

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

This is the step the whole Confidential claim rests on, so it is worth stating
precisely. At this point the browser holds the plaintext, the challenge it
generated, and a public key the workload *claims* is its own. It releases none of
them until every check below has passed.

**Signature.** `verifyConfidentialSpaceToken` in
`packages/client/src/confidential-attestation.ts` verifies the token as an RS256
JWT against Google's published JWKS for
`signer@confidentialspace-sign.iam.gserviceaccount.com`, requiring issuer
`https://confidentialcomputing.googleapis.com` and the audience from policy. The
JWKS URL and the issuer are constants in the client source. An unsigned,
wrongly-signed, wrong-issuer, or wrong-audience token fails here, before a single
claim is read.

**Where the policy comes from.** The claim checks run against a
`ConfidentialPolicy` — approved image digests, GCP project numbers, service
accounts, hardware models, and the audience — supplied by the embedding
application as build-time configuration. The website embeds the exported
`releases/approved-workloads.json` allowlist when it builds; the browser never
fetches it at runtime and never learns any part of it from the workload.
`/v1/info` is informational and is not consulted. Any policy field that parses to
an empty set raises `ConfidentialPolicyConfigurationError` instead of defaulting
to permissive, so an unconfigured or mis-parsed policy fails closed rather than
accepting everything.

**Required security claims** (`validateConfidentialSpaceClaims`), every one
mandatory:

- `swname = CONFIDENTIAL_SPACE` — genuinely Confidential Space, not another TEE
  flavour or a plain VM
- `dbgstat = disabled-since-boot` — no debug access at any point since boot
- `secboot = true` — Secure Boot attested
- `submods.confidential_space.support_attributes` contains `STABLE` — the
  production Confidential Space image, not a debug image
- `submods.confidential_space.monitoring_enabled.memory = false` — workload
  memory monitoring off, so enclave memory contents are not exported to Cloud
  Monitoring
- `hwmodel` in the approved hardware-model set
- `submods.gce.project_number` in the approved project set
- `google_service_accounts` intersects the approved service-account set
- `submods.container.cmd_override` and `env_override` — absent or empty only. A
  null, malformed, or non-empty value is rejected. Absence is accepted because
  Google omits these claims entirely when no override event occurred; the client
  README records the production image this was observed on.

**Approved image digest.** `submods.container.image_digest` must be a
`sha256:`-prefixed digest present in the policy's approved set. This is the check
that ties the code actually running to the code an operator reviewed: an image
built from different source, or one since revoked, has a different digest and is
refused here even when the token is genuinely Google-signed and every other claim
is perfect.

**Challenge / request / key binding.** The browser recomputes

```
SHA-256("simpleunmark-attestation-v2\n" + requestId + "\n" + challenge + "\n" + publicKey)
```

locally (`attestationBinding` in `confidential-crypto.ts`) from *its own*
challenge, *its own* request ID, and the public key just returned, and requires
the result to appear in the token's `eat_nonce` (accepted as either a string or
an array, both being valid encodings). This binding is what makes the token
non-replayable and non-relayable: a stale token fails because the challenge
differs, and a proxy holding a genuine token for the real workload cannot
substitute an encryption key of its own, because the key is inside the hash the
token commits to.

**Encryption happens only after all of the above succeeds.** Verification is not
advisory and is not raced against the upload; the plaintext is passed to
`confidential-crypto.ts` only on the success path. `validateConfidentialSpaceClaims`
is exported separately for tests and performs no signature verification at all;
production paths call `verifyConfidentialSpaceToken`.

The browser is not the only thing validating. Two further surfaces matter, and
both live in the workload:

- **Capability validation** (`security.py`). Before anything else happens, the
  workload verifies the capability's HMAC with `compare_digest`, then enforces
  `aud = simpleunmark-confidential-server`, a known version, `iat` not in the
  future beyond 30 s of skew, an unexpired `exp`, a lifetime of at most ten
  minutes, the declared mode against the endpoint, and the asset-kind invariants:
  text capabilities may carry no file metadata at all, and media capabilities
  must be v2 Confidential with a lowercase alphanumeric extension, a printable
  MIME type, an operation, and zero text measurements. `AUDIO_PURIFY` is refused
  on anything but audio.
- **Measurement checks** (`_validated_text`, and the media comparison in
  `clean_encrypted_media`). The decrypted content must match the capability
  exactly — word, character and byte counts for text; asset kind, extension, MIME
  type, operation and byte count for media. A mismatch is a `403`, never a
  silent re-price. Because the browser computed those numbers and the website
  signed them, this is what stops a larger file or a different operation being
  substituted under an authorization issued for something cheaper.

Accounting then rides on **metadata-only receipts**: `_receipt_base` and
`_telemetry` in `app.py` construct the entire body, and it contains counts, mode,
asset kind, operation, status, request ID, and token/cost/timing telemetry. No
input, no output, no prompt, no filename, no removed metadata value, no content
fragment. The website bills from measurements it already signed, so it never
needs to see what was cleaned — see [§7](#7-encrypted-response-and-accounting)
for what that path does and does not guarantee.

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

Every guard runs before any billable cost is incurred. Memory and CPU are a
different question — see the note after the list.

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
8. Media only: if a cleaning job is already running, `429 BUSY`, returned
   *before* the reservation is claimed, so the browser can abandon and retry at
   no cost.
9. The signed, metadata-only `started` receipt atomically claims the reservation.
   Only then is the attestation entry dropped and the ephemeral key released.

**What the single-job lock does and does not bound.** `media_processing_lock`
serializes *cleaning*: at most one FFmpeg or image job runs at a time, so
simultaneous transcodes cannot exhaust the two vCPUs or the enclave's memory. It
does not bound upload memory. The busy check is step 8, and by the time it runs
the request body has already
been read fully into memory (`_read_encrypted_media_input`, up to
`max_media_bytes` plus a 4 KiB envelope allowance — currently 50 MiB), copied
once from the streaming `bytearray` into `bytes`, and HPKE-decrypted into a third
plaintext buffer. Concurrent uploads therefore each hold their own buffers in
enclave RAM and are rejected only after that cost has been paid. What limits how
many can be in flight at once is the load balancer, the ASGI server, and the VM's
memory — not this lock. The same is true of concurrent text requests, at the much
smaller `max_body_bytes` scale of 400 KB each.

### 6. Processing

Text (`text.py`) runs the pinned upstream `watermarks-remover` v0.7.0
deterministic pass in conservative mode — normalizing exotic spaces and removing
unsupported invisible carriers while preserving load-bearing emoji glue, script
joiners, variation selectors, subdivision flags, and bidi controls — then one
DeepInfra paraphrase. **The paraphrase leaves the confidential boundary:**
DeepInfra receives the deterministically cleaned source text, and returns the
rewrite, in plaintext over its normal authenticated HTTPS API. See
[The confidentiality boundary](#the-confidentiality-boundary) for what protects
it there and what does not.

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

In parallel, the workload posts HMAC-signed receipts to the website: `started`
when the reservation is claimed, then `succeeded` or `failed` with an error code.
A receipt carries counts, mode, asset kind, operation, status, request ID, and
token/cost/timing telemetry — no input, output, prompt, filename, removed
metadata value, or content fragment (`_receipt_base` and `_telemetry` in
`app.py`). That body is the complete accounting record; nothing else crosses back
to the website.

**Terminal receipt delivery is best-effort, and this is where the accounting
guarantees stop.** `ReceiptClient.submit` retries up to three times with backoff
and gives up immediately on `400`, `401`, `403`, or `409`; an exhausted or
refused submission raises `ReceiptError`. The `started` receipt is the clean
case: it is sent with `attempts=1`, and a failure there aborts the request with
`409` before anything is spent. The terminal receipts behave differently:

- Media path: a `failed` receipt that cannot be delivered is swallowed
  (`except ReceiptError: pass`), and the browser is still told "Reserved credits
  were returned."
- Text path: a `failed` receipt that cannot be delivered after a processing error
  leaves `balances = None`, and the browser is still told "Any reserved credits
  will be returned."
- Client-abort path: the `failed` receipt is shielded against cancellation, but a
  `ReceiptError` there is likewise swallowed.
- Success path: if the `succeeded` receipt cannot be delivered, the request is
  converted into a failure — the cleaned text or file is discarded, a
  `PROCESSING_FAILED` receipt is attempted in its place, and the user sees an
  error for work that actually completed.

So a `started` receipt can be recorded with no terminal receipt ever following
it. The same happens with no code path involved at all if the process stops
between the two: an OOM kill, an unhandled crash, or the wholesale instance
replacement that every image or secret change performs. Receipt state is
process-local and is journaled nowhere, so a restarted workload has no knowledge
of in-flight requests and will never retry them.

**The website must therefore reconcile reservations that have a `started` receipt
and no terminal receipt** — by timeout, by periodic sweep, or both. The workload
cannot do it. Read every refund statement in this document accordingly: each
failure path *attempts* a refund, and a claimed reservation whose terminal
receipt never arrived stays claimed until the website resolves it.

**A rewrite failure does not always return the deterministic result.** When the
provider yields no billable text, the workload submits a `REWRITE_FAILED` receipt
and then hands back the deterministic output with a warning and zero credits
charged — but only when both conditions hold:

1. The deterministic pass actually changed something (`removed` or
   `normalized_spaces` non-zero). If it changed nothing there is nothing useful
   to return, and the browser gets an error event instead.
2. That `REWRITE_FAILED` receipt was accepted. Its submission is not wrapped in
   its own `try`, so a `ReceiptError` propagates to the generic handler, which
   emits a `PROCESSING_FAILED` receipt and an error event — discarding the
   deterministic text the workload had already computed.

A provider stream that fails *after* output has begun takes that same generic
path, and a client abort returns nothing at all. In each of these cases any
`delta` events already streamed to the browser are superseded by the terminal
event rather than completed.

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
record: a distinct, timestamped, attributable action that cannot happen as a side
effect of the merge. It is not independent review — see
[Role separation today](#role-separation-today).

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

## Role separation today

The release chain has four gates. It does not have four people.

| Gate | Mechanism | Who performs it today |
| --- | --- | --- |
| Implementation | Commits and PRs against `main` | The single maintainer |
| Review | PR review of code, dependencies and provenance | The same person |
| Approval | `release-approval` environment job signing the policy file | The same person |
| Deployment | `terraform plan` / `apply` from a workstation | The same person |

`infra/github/main.tf` makes this explicit rather than leaving it implied. While
`independent_review_required` is `false` — its default and the current setting —
`required_approving_review_count` is `0`, `require_last_push_approval` is off,
and `prevent_self_review` is off for both the `production` and `release-approval`
environments. The repository owner is the only account with write access. The
same person therefore writes the code, approves the PR, clicks approve on the
environment job, and runs `apply`.

**Separate workflow gates do not constitute independent review.** What the four
gates actually buy is sequencing and evidence. Each step is a distinct,
attributable, timestamped action that cannot occur implicitly as a side effect of
the previous one, and the approval attestation signs the exact policy file so the
record cannot be rewritten afterwards. That is genuinely valuable, and it defeats
a broad class of accidents: a stray tag publishing to production, `latest`
drifting under a running VM, an unreviewed digest reaching a browser, a
deployment of something nobody chose. It is not a second pair of eyes. A mistake
or a deliberate act by the maintainer passes every gate, because the maintainer
*is* every gate — and an attacker who compromises that one account compromises
the entire chain in a single step. Treat every "separately gated" statement in
this document as a claim about steps, not about people.

**The roles are designed to be split, and splitting them is configuration, not
redesign.** Adding a second maintainer with write access and setting
`independent_review_required = true` in a local `*.auto.tfvars` raises
`required_approving_review_count` to `1`, enables `require_last_push_approval`,
and turns on `prevent_self_review` for both environments. At that point the
person who wrote a change can no longer approve its PR, and the person who merged
it can no longer approve its release job. Deployment can be separated further by
giving the runtime Terraform credentials to an operator other than the one
holding repository write access. The flag is deliberately not enabled while solo,
because it would block the only maintainer from releasing at all. None of this
defends against a repository or cloud administrator who changes the protections
themselves.

**Splitting the roles does not change what the browser trusts.** Even with
independent review in place, the website still serves the JavaScript that
performs the verification described in
[step 3](#3-browser-side-verification). A compromised or coerced website can ship
client code that skips the attestation check entirely, or that exfiltrates
plaintext before encrypting it, and no amount of separation in the release chain
detects that. Role separation raises the bar for shipping a bad *workload image*;
it does nothing about delivery of the *client*. That limit is unchanged, and it
remains the reason an independently distributed, pinned client is the strongest
available follow-up.

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
| Reservation claimed before spend; `429` before claim | `app.py` | Double-spend and charging for rejected work. Terminal receipts are best-effort — see [§7](#7-encrypted-response-and-accounting) |
| Single-job media lock | `media_processing_lock` | Concurrent transcodes exhausting enclave CPU and memory. **Not** upload buffering |
| Baked-in production config | Dockerfile `ENV` | Silent endpoint/model/audience changes without a new digest |
| Launch policy + attested override claims | Image labels *and* browser | Command/env override, log redirect, memory monitoring |
| In-memory, CRC-checked secret bootstrap | `bootstrap.py` | Credentials on disk, in env, or inherited by FFmpeg |
| Dual-registry digest equality, single-platform check | Release workflow | Registry divergence; an index instead of a measured manifest |
| Pinned provenance verification | `release-policy.mjs` | Forged or foreign-built artifacts; self-hosted runners |
| Transition rules | `validateTransition` | History deletion, evidence rewriting, un-revocation |
| Separate approval environment | `approve-release-policy.yml` | Publication implying approval. Sequencing and evidence, not independent review |
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
- DeepInfra receives the submitted text in plaintext, after the deterministic
  pass and over HTTPS. Its protections are provider policy — including an
  explicit reservation to log a small sample of request content for debugging or
  security — not cryptography, and the browser cannot verify any of them.
  Self-hosted inference on an attested confidential GPU is planned and deferred
  for cost, so Confidential AI is a planned mode, not a shipped guarantee. Full
  detail in [The confidentiality boundary](#the-confidentiality-boundary).
- Implementation, review, approval, and deployment are all performed by one
  person today. The four gates give sequencing and an audit trail, not
  independent review; the configuration to separate them exists and is off. See
  [Role separation today](#role-separation-today).
- Accounting is eventually consistent at best. A `started` receipt with no
  terminal receipt — undelivered after retries, or lost to process termination —
  leaves a reservation claimed that only the website can reconcile. Not every
  failure results in a refund on its own, and not every rewrite failure returns
  the deterministic result.
- The single-job media lock bounds concurrent cleaning, not concurrent uploads.
  Each in-flight upload holds its own ciphertext and plaintext buffers in enclave
  memory before the busy check rejects it.
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
