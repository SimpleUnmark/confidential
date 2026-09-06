import { describe, expect, it } from "vitest";
import { validateConfidentialSpaceClaims as validateClaims, type ConfidentialPolicy } from "../src/confidential-attestation";

const digest = `sha256:${"a".repeat(64)}`;
const nonce = "fresh-browser-nonce";

function claims(overrides: Record<string, unknown> = {}) {
  return {
    swname: "CONFIDENTIAL_SPACE",
    dbgstat: "disabled-since-boot",
    secboot: true,
    hwmodel: "GCP_AMD_SEV",
    eat_nonce: [nonce],
    google_service_accounts: ["workload@operator-project.iam.gserviceaccount.com"],
    submods: {
      confidential_space: {
        support_attributes: ["STABLE", "USABLE"],
        monitoring_enabled: { memory: false },
      },
      container: {
        image_digest: digest,
        cmd_override: [],
        env_override: {},
      },
      gce: { project_number: "123456789012" },
    },
    ...overrides,
  };
}

const policy: ConfidentialPolicy = {
  imageDigests: digest,
  projectNumbers: "123456789012",
  serviceAccounts: "workload@operator-project.iam.gserviceaccount.com",
  hardwareModels: "GCP_AMD_SEV",
  audience: "https://simpleunmark.com/confidential-workload/v1",
};

function validateConfidentialSpaceClaims(payload: Parameters<typeof validateClaims>[0], value: string) {
  return validateClaims(payload, value, policy);
}

describe("validateConfidentialSpaceClaims", () => {
  it.each(["imageDigests", "projectNumbers", "serviceAccounts", "hardwareModels"] as const)(
    "rejects a missing %s policy instead of learning it from the token",
    (field) => {
      expect(() => validateClaims(claims(), nonce, { ...policy, [field]: "" })).toThrow();
    },
  );

  it("does not share policy between independently configured clients", () => {
    expect(() => validateClaims(claims(), nonce, { ...policy, projectNumbers: "999" }))
      .toThrow("unapproved GCP project");
    expect(validateClaims(claims(), nonce, policy)).toMatchObject({ imageDigest: digest });
  });

  it("accepts a locked production workload with the expected identities", () => {
    expect(validateConfidentialSpaceClaims(claims(), nonce)).toEqual({
      imageDigest: digest,
      projectNumber: "123456789012",
      hardwareModel: "GCP_AMD_SEV",
    });
  });

  it("rejects command or environment overrides", () => {
    const base = claims();
    expect(() => validateConfidentialSpaceClaims({
      ...base,
      submods: {
        ...(base.submods as object),
        container: { ...(base.submods.container as object), env_override: { PORT: "9000" } },
      },
    }, nonce)).toThrow("environment setting was overridden");
  });

  it("rejects a copy of the image in an unapproved project", () => {
    const base = claims();
    expect(() => validateConfidentialSpaceClaims({
      ...base,
      submods: {
        ...(base.submods as object),
        gce: { project_number: "999999999999" },
      },
    }, nonce)).toThrow("unapproved GCP project");
  });

  it("rejects enabled memory monitoring", () => {
    const base = claims();
    expect(() => validateConfidentialSpaceClaims({
      ...base,
      submods: {
        ...(base.submods as object),
        confidential_space: {
          support_attributes: ["STABLE"],
          monitoring_enabled: { memory: true },
        },
      },
    }, nonce)).toThrow("memory monitoring is enabled");
  });

  it.each([
    ["a missing nonce", { eat_nonce: ["another-nonce"] }, "nonce does not match"],
    ["a missing hardware model", { hwmodel: undefined }, "hardware model is not approved"],
    ["an unapproved hardware model", { hwmodel: "GCP_INTEL_TDX" }, "hardware model is not approved"],
    ["debug mode", { dbgstat: "enabled" }, "Debug mode is enabled"],
    ["disabled Secure Boot", { secboot: false }, "Secure Boot is not attested"],
    ["the wrong workload image", { swname: "CONFIDENTIAL_VM" }, "Not Confidential Space"],
  ])("rejects %s", (_name, override, message) => {
    expect(() => validateConfidentialSpaceClaims(claims(override), nonce)).toThrow(message);
  });

  it("accepts Google's documented scalar nonce encoding", () => {
    expect(validateConfidentialSpaceClaims(claims({ eat_nonce: nonce }), nonce))
      .toMatchObject({ imageDigest: digest });
  });

  it("requires a production Confidential Space support attribute", () => {
    const base = claims();
    expect(() => validateConfidentialSpaceClaims({
      ...base,
      submods: {
        ...(base.submods as object),
        confidential_space: {
          support_attributes: ["USABLE"],
          monitoring_enabled: { memory: false },
        },
      },
    }, nonce)).toThrow("production Confidential Space image is not attested");
  });

  it("rejects an absent or unapproved workload image digest", () => {
    const base = claims();
    for (const imageDigest of [undefined, `sha256:${"b".repeat(64)}`]) {
      expect(() => validateConfidentialSpaceClaims({
        ...base,
        submods: {
          ...(base.submods as object),
          container: { ...(base.submods.container as object), image_digest: imageDigest },
        },
      }, nonce)).toThrow("image digest is not approved");
    }
  });
});
