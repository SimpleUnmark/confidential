import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

const GOOGLE_ISSUER = "https://confidentialcomputing.googleapis.com";
const GOOGLE_JWKS = createRemoteJWKSet(
  new URL(
    "https://www.googleapis.com/service_accounts/v1/metadata/jwk/signer@confidentialspace-sign.iam.gserviceaccount.com",
  ),
);

export class ConfidentialPolicyConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfidentialPolicyConfigurationError";
  }
}

/** Trusted local policy, never populated from the workload's /v1/info response. */
export type ConfidentialPolicy = {
  imageDigests: string;
  projectNumbers: string;
  serviceAccounts: string;
  hardwareModels: string;
  audience: string;
};

function expectedDigests(raw: string) {
  const digests = raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => /^sha256:[a-f0-9]{64}$/.test(value));
  if (!digests.length) {
    throw new ConfidentialPolicyConfigurationError(
      "No confidential workload image digest is configured.",
    );
  }
  return new Set(digests);
}

function expectedValues(raw: string, name: string, pattern: RegExp) {
  const values = raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => pattern.test(value));
  if (!values.length) {
    throw new ConfidentialPolicyConfigurationError(`${name} is not configured.`);
  }
  return new Set(values);
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

// Claims-only validation is useful for tests, but is not signature verification.
// Production clients must call verifyConfidentialSpaceToken instead.
export function validateConfidentialSpaceClaims(
  payload: JWTPayload,
  nonce: string,
  policy: ConfidentialPolicy,
) {
  const submods = record(payload.submods);
  const container = record(submods.container);
  const confidentialSpace = record(submods.confidential_space);
  const gce = record(submods.gce);
  const supportAttributes = confidentialSpace.support_attributes;
  const monitoring = record(confidentialSpace.monitoring_enabled);
  // Google documents eat_nonce as either one string or an array. The current
  // request asks for an array, but accepting both valid encodings avoids a
  // fail-closed outage if the attestation service canonicalizes one nonce.
  const tokenNonces = Array.isArray(payload.eat_nonce)
    ? payload.eat_nonce
    : typeof payload.eat_nonce === "string"
      ? [payload.eat_nonce]
      : [];
  const imageDigest = container.image_digest;
  const environmentOverrides = container.env_override;
  const commandOverrides = container.cmd_override;
  const projectNumber = gce.project_number;
  const serviceAccounts = payload.google_service_accounts;
  const hardwareModel = payload.hwmodel;
  const approvedProjects = expectedValues(
    policy.projectNumbers,
    "projectNumbers",
    /^\d+$/,
  );
  const approvedServiceAccounts = expectedValues(
    policy.serviceAccounts,
    "serviceAccounts",
    /^[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com$/,
  );
  const approvedHardwareModels = expectedValues(
    policy.hardwareModels,
    "hardwareModels",
    /^GCP_[A-Z0-9_]+$/,
  );

  if (payload.swname !== "CONFIDENTIAL_SPACE") throw new Error("Not Confidential Space.");
  if (payload.dbgstat !== "disabled-since-boot") throw new Error("Debug mode is enabled.");
  if (payload.secboot !== true) throw new Error("Secure Boot is not attested.");
  if (!Array.isArray(supportAttributes) || !supportAttributes.includes("STABLE")) {
    throw new Error("The production Confidential Space image is not attested.");
  }
  if (monitoring.memory !== false) {
    throw new Error("Workload memory monitoring is enabled.");
  }
  if (typeof hardwareModel !== "string" || !approvedHardwareModels.has(hardwareModel)) {
    throw new Error("The confidential hardware model is not approved.");
  }
  if (typeof imageDigest !== "string" || !expectedDigests(policy.imageDigests).has(imageDigest)) {
    throw new Error("The workload image digest is not approved.");
  }
  if (!tokenNonces.includes(nonce)) throw new Error("The attestation nonce does not match.");
  // The launcher emits one override event per argument/environment entry.
  // Google can omit these claims entirely when there are no override events
  // (observed on the production 260701 image), instead of returning [] / {}.
  // Accept omission, but never coerce null, malformed, or nonempty claims.
  // The signature, pinned image, production/debug state and nonce checks above
  // remain mandatory; approved images must also deny overrides in launch policy.
  if (commandOverrides !== undefined
    && (!Array.isArray(commandOverrides) || commandOverrides.length !== 0)) {
    throw new Error("The workload command was overridden.");
  }
  if (environmentOverrides !== undefined && (
    environmentOverrides === null
    || typeof environmentOverrides !== "object"
    || Array.isArray(environmentOverrides)
    || Object.keys(environmentOverrides).length !== 0
  )) {
    throw new Error("A workload environment setting was overridden.");
  }
  if (typeof projectNumber !== "string" || !approvedProjects.has(projectNumber)) {
    throw new Error("The workload is running in an unapproved GCP project.");
  }
  if (
    !Array.isArray(serviceAccounts)
    || !serviceAccounts.some(
      (account) => typeof account === "string" && approvedServiceAccounts.has(account),
    )
  ) {
    throw new Error("The workload service account is not approved.");
  }

  return { imageDigest, projectNumber, hardwareModel };
}

export async function verifyConfidentialSpaceToken(
  token: string,
  nonce: string,
  policy: ConfidentialPolicy,
) {
  const audience = policy.audience.trim();
  if (!audience) throw new ConfidentialPolicyConfigurationError("audience is not configured.");
  const { payload } = await jwtVerify(token, GOOGLE_JWKS, {
    algorithms: ["RS256"],
    issuer: GOOGLE_ISSUER,
    audience,
  });
  return validateConfidentialSpaceClaims(payload, nonce, policy);
}
