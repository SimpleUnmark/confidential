# Confidential service

This package is the only Simple Unmark server component allowed to decrypt user
content in Confidential mode. It is a small FastAPI/HTTPX application intended to run inside Google
Confidential Space.

## API and cryptographic protocol

- `GET /healthz` — liveness only; no configuration or secrets
- `GET /v1/info` — protocol and cleaner versions; informational only
- `POST /v1/attestation` — accepts a browser challenge and returns the
  workload's request-scoped HPKE public key plus a Google Confidential Space token
- `POST /v1/clean` — accepts an HPKE encapsulated key and ciphertext, then
  streams AES-GCM-encrypted newline-delimited JSON envelopes
- `POST /v1/media/clean` — accepts an HPKE-encrypted binary media frame, routes
  it to the image or audio/video cleaner, optionally runs the destructive audio
  pass, and returns one AES-GCM-encrypted binary result
- `POST /v1/plain/clean` — accepts plaintext from the trusted web backend for
  Private mode and streams plaintext newline-delimited JSON events

For each attestation the workload derives a fresh RFC 9180 key for
`DHKEM(P-256, HKDF-SHA256) / HKDF-SHA256 / AES-128-GCM`. The private key remains
in process memory, is retained only for the capability lifetime so an invalid
ciphertext can be retried, and is dropped once the reservation is claimed. The token nonce is
`SHA-256(protocol || request ID || browser challenge || public key)`, preventing
an intermediary from substituting a public key that is not inside the attested
workload.

The request uses HPKE base mode. A 32-byte AES-GCM response key and 12-byte base
nonce are derived with the HPKE exporter. Every response event has a strictly
increasing sequence number, a sequence-derived nonce, and authenticated data
that includes the request ID and sequence. Reordered, duplicated, changed, or
cross-request events fail authentication.

Identical concurrent attestation retries for one authorization share a single
launcher token request. Production token requests are also paced below the
default Confidential Space custom-token quota. Both the ephemeral-key cache and
the pacing state are process-local, so v1 must remain a single-instance service
unless those controls are coordinated across replicas.

The cleaning capability is short-lived, origin-bound, mode-bound, and
HMAC-signed. A Private capability is rejected by the Confidential endpoint and
vice versa. Before
model processing, the workload makes a signed metadata-only `started` callback
that atomically consumes the capability. Success and failure callbacks contain
counts, token/cost/timing telemetry, status, and request ID, but no input,
output, prompt, filename, removed metadata value, or content fragment.

The media protocol avoids base64-expanding large files. The outer request is
`SUME1 || uint16(enc length) || HPKE enc || ciphertext`; its plaintext is
`SUMM1 || uint32(header length) || JSON header || file bytes`. The encrypted
response contains `SUMR1 || uint32(header length) || JSON result || file bytes`.
The signed capability binds the media category, operation, extension, MIME type,
and exact original byte count to prevent operation or type substitution and
under-billing.

## Runtime credential bootstrap

Production (`REQUIRE_CLIENT_ATTESTATION=1`) reads two numeric Secret Manager
version references from GCE metadata:

- `simpleunmark-deepinfra-secret-version`
- `simpleunmark-shared-secret-version`

It obtains a short-lived OAuth token from the attached VM service account and
fetches both values directly over HTTPS from Secret Manager. Secret payloads are
CRC32C-checked and held in Python memory, not written to disk or environment
variables inherited by FFmpeg. The service does not start if either reference,
permission, enabled version, checksum, or credential format is invalid. It does
not fall back to plaintext metadata or environment credentials in production.
Local development still reads the two credentials from the environment.

Terraform creates only secret containers, version references, and secret-scoped
IAM grants. The operator uploads values directly; see
[the migration and rotation guide](../../infra/gcp/secret-manager-migration.md).
Explicit numeric version pins ensure startup is reproducible; Terraform replaces
the workload when a version changes so it reloads the credentials.

No customer-managed KMS key, STS, runtime workload identity pool, or downloaded
service-account key is required. This is **not attestation-gated secret release**.
Administrators able to change IAM or reuse/impersonate the workload identity can
still access the secrets. A leaked DeepInfra key exposes the provider account.
A leaked HMAC key permits forged authorizations and receipts, but neither key
decrypts recorded HPKE payloads.

## Upstream cleaner

The deterministic text and media passes use the upstream
[`watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover)
v0.7.0 `text_unicode.py`, `image_meta.py`, `av_meta.py`, and `clean_audio.py`, pinned to immutable commit
`321d93d2efd6a8b26915c5eb5193d9d1701e2c4b`. The default conservative mode
normalizes exotic spaces and removes unsupported invisible carriers and
noncharacters while contextually preserving load-bearing emoji, script
joiners, variation selectors, subdivision flags, bidirectional formatting,
and visible-layout controls. The browser cleaner is pinned to the same behavior
with shared fixtures.

The media router supports PNG, JPEG, WebP, AVIF, HEIC, BMP, GIF, TIFF, MP4,
MOV, M4V, WAV, MP3, FLAC, and M4A. Each request uses a temporary directory that
is deleted before the response is returned. The production Terraform mounts
that directory as Confidential Space tmpfs, keeping scratch bytes in protected
VM memory rather than the writable boot-disk partition. The recommended
metadata pass strips container metadata and provenance without intentionally
re-encoding pixels, frames, or waveforms. It can also remove orientation and
colour-profile fields, so the same media bytes can display differently in some
viewers and users must review the download. The image includes ffmpeg/ffprobe
for a separate opt-in audio operation: after stripping metadata, it applies
1.08× tempo, a +2-semitone pitch shift, EQ, and a 96 kbps AAC re-encode, then
strips the resulting M4A metadata again. This materially changes pitch,
duration, and quality. It is designed to exceed the survival ranges cited by
upstream v0.7.0, but Simple Unmark ships no vendor detector and does not
guarantee removal. No image/video diffusion model is included.

The two-vCPU v1 workload admits one media job at a time. A concurrent request
receives `429 BUSY` before its credit reservation is claimed, so the browser
can abandon the reservation and retry. Media uploads have a 120-second body
window, the destructive audio subprocess is capped at 240 seconds, and the GCP
load balancer allows 420 seconds for the complete exchange.

Upstream v0.7.0 also offers a Layer B strategy that mixes an LLM paraphrase
with a local `roberta-large` masked-language model. The confidential service
does not ship that multi-gigabyte model: it keeps the Distroless workload small
and performs one DeepInfra paraphrase using the upstream v0.7 paraphrase tactic.

## Threat boundary

In Confidential mode, the design keeps plaintext content out of the Next.js
application, its database, application logs, Telegram notifications, HTTP
receipt callbacks, and an external HTTPS load balancer. Private mode deliberately
uses the simpler trust boundary: the Next.js application proxies plaintext to
`/v1/plain/clean`. Neither mode stores submitted or cleaned text in the
application database. Distroless/nonroot and Confidential Space reduce the
ability of a workload or host operator to inspect the running process.

The guarantee has explicit limits:

- DeepInfra receives and returns plaintext over its normal authenticated HTTPS
  API. It is not protected by this HPKE layer.
- Secret Manager limits routine credential access but does not protect these
  credentials from administrators who can grant access or reuse the service identity.
- Request counts, destination, timing, and ciphertext sizes remain observable.
- Media category, operation, extension, and exact byte size remain visible to the
  authorization layer.
- Media metadata cleanup does not remove visible overlays or signals embedded
  in pixels, frames, or waveforms. The destructive audio pass may disrupt
  waveform signals but does not guarantee a result against any detector.
- Metadata fields that affect presentation, including image orientation and
  colour profiles, can be removed even though pixels are not re-encoded.
- A maliciously changed website can exfiltrate text before encryption. Browser
  verification removes trust in the web backend's attestation decision, but it
  cannot make the JavaScript that the same site serves immutable. A signed
  extension/native client or independently pinned frontend is the stronger
  follow-up.
- A TEE reduces infrastructure access; bugs or intentional exfiltration in an
  approved image remain trusted. Review and pin the released OCI digest.

## Local verification

```bash
pnpm server:setup
set -a
. apps/confidential-server/.env
set +a
pnpm --filter @simpleunmark/confidential-server test
pnpm --filter @simpleunmark/confidential-server lint
pnpm --filter @simpleunmark/confidential-server typecheck
```
