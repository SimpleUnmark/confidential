# Confidential browser client

Framework-independent TypeScript source for Google Confidential Space token
verification, HPKE request encryption, response decryption, protocol types,
Unicode inspection, and deterministic text measurements. Consumers need a
TypeScript-aware bundler; Next.js users should add
`@simpleunmark/confidential-client` to `transpilePackages`.

The client has no environment-variable or Next.js dependency. Supply an explicit
`ConfidentialPolicy` from trusted application configuration, containing
comma-separated `imageDigests`, `projectNumbers`, `serviceAccounts`, and
`hardwareModels`, plus the exact `audience`. Never learn trusted identities from
the workload itself. An empty allowlist fails closed.

Before encrypting, generate a challenge, request the workload attestation, derive
`attestationBinding(challenge, requestId, encryption.publicKey)`, and call
`verifyConfidentialSpaceToken(token, nonce, policy)`. Then use
`encryptCleanRequest` or `encryptMediaRequest`, which checks the public key's
fingerprint and supported suite. `validateConfidentialSpaceClaims` checks claims
only; it must not replace JWT signature verification.

From the repository root, run `pnpm install`,
`pnpm --filter @simpleunmark/confidential-client test`, and `pnpm client:pack`.
The resulting tarball in `artifacts/` contains only package metadata, documentation,
license, and `src/`. The release workflow attests this tarball as well as the OCI
image. The website can vendor a reviewed tarball and pin its integrity in its
lockfile, with no sibling-checkout or private Git dependency at build time.

This code does not remove trust in website-delivered JavaScript. Independently
distributed, pinned clients are a separate stronger trust model.
