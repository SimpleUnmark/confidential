# SimpleUnmark Confidential

Open-source confidential computing workloads, browser verification, and GCP
deployment infrastructure for [SimpleUnmark](https://simpleunmark.com).
The website, accounts, payments, and database live in a separate private repository.

```text
apps/confidential-server/  Python FastAPI service and Distroless Dockerfile
packages/client/          Framework-independent TypeScript verifier and HPKE client
fixtures/                 Shared browser/Python deterministic-cleaning fixtures
infra/gcp/foundation/     Terraform: APIs, public registry, GitHub publishing IAM
infra/gcp/terraform/      Terraform: Confidential Space VM, networking, HTTPS
infra/github/             Terraform: protected release environments and CI variables
releases/                 Reviewed approved/revoked image policy
docs/                     Architecture, security, and release/deployment guides
```

## What it protects

In Confidential mode, the browser verifies a Google-signed attestation binding an
approved workload image to a request-scoped public key. Text or media is encrypted
directly to that key. Response content is encrypted back to the browser. The
website authorizes requests and records metadata-only accounting receipts without
receiving Confidential-mode content. Private mode uses an HTTPS plaintext proxy
through the website to this same workload.

Text rewriting still sends plaintext to DeepInfra over HTTPS. Image/video/audio
metadata cleaning stays inside the workload. An opt-in destructive audio operation
changes tempo, pitch, EQ, and encoding; it does not guarantee watermark removal.
Visible image/video overlay removal and confidential GPU inference are **not
implemented**. Confidential AI is a planned mode, not a current security guarantee.

The [confidential architecture](docs/architecture.md) documents the whole chain:
request-time attestation and encryption, the release and approval gates, and
where each safeguard is enforced.

The browser-delivered JavaScript and approved workload code remain trusted.
The two runtime credentials are fetched directly from Secret Manager using the
VM service account. Terraform and VM metadata contain version references only,
not credential values. Secret access is IAM-based, not attestation-gated;
sufficiently privileged administrators can still obtain these credentials.
They do not decrypt HPKE payloads. See the [full threat boundary](apps/confidential-server/README.md#threat-boundary).

## Local development

Use Node.js 22+ with pnpm `11.18.0`, Python 3.13, and ffmpeg/ffprobe for media tests.
From this repository root:

```bash
pnpm install
pnpm server:setup
pnpm lint
pnpm typecheck
pnpm test
```

Copy `apps/confidential-server/.env.example` to its ignored `.env`, fill in local
credentials, and load it before starting the service:

```bash
set -a
. apps/confidential-server/.env
set +a
pnpm dev:server
```

`host.docker.internal` in the example callback URL is for Docker Desktop. For a
native Python service and local website, use `http://127.0.0.1:3000/api/clean/receipt`.
Actual cleaning requires a compatible authorization/receipt backend; `/healthz`
and `/v1/info` do not. Never disable attestation in a production build.

The public client has no private-repository dependency. `pnpm client:pack` builds
an allowlisted source tarball in `artifacts/`; see its [integration guide](packages/client/README.md).

## Build and deploy

```bash
docker build --platform linux/amd64 -f apps/confidential-server/Dockerfile \
  -t simpleunmark-confidential apps/confidential-server
```

The upstream cleaner is pinned to `watermarks-remover` v0.7.0 commit
`321d93d2efd6a8b26915c5eb5193d9d1701e2c4b`. The image contains its MIT license
and the distribution's ffmpeg license notices. This repository and client are
MIT-licensed; bundled dependencies retain their respective licenses.

Follow the [GCP deployment guide](infra/gcp/README.md) and
[deployment checklist](docs/deployment-checklist.md). The public release workflow
tests both components, builds once and publishes identical digests to Docker Hub
and public-read GCP Artifact Registry, and signs provenance for both images and
the client tarball. GCP authentication uses short-lived GitHub OIDC federation,
not a stored GCP key. No private checkout or website credentials are needed.
Follow [release approval and rotation](docs/release-security.md); publication
never automatically approves a digest or deploys it.

All three Terraform directories pin `1.15.8` and support plain `terraform init`,
`terraform plan`, and `terraform apply` from inside the directory. Existing
production targets Belgium (`europe-west1`); the state bucket and retained
rollback registry stay in Frankfurt. Follow the
[Belgium migration](infra/gcp/belgium-migration.md) for the existing deployment.
DNS is configured manually using
the runtime's `public_ip` output. Operators review and execute all cloud changes
themselves.

For the initial repository split, read [migration notes](docs/repository-migration.md)
before running Terraform or publishing a release.
