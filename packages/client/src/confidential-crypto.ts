import {
  Aes128Gcm,
  CipherSuite,
  DhkemP256HkdfSha256,
  HkdfSha256,
} from "@hpke/core";
import type { CleanStreamEvent } from "./clean-protocol";

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const HPKE_INFO = encoder.encode("simpleunmark-hpke-v1");
const RESPONSE_KEY_LABEL = encoder.encode("simpleunmark-response-key-v1");
const RESPONSE_NONCE_LABEL = encoder.encode("simpleunmark-response-nonce-v1");

export const CONFIDENTIAL_HPKE_SUITE =
  "DHKEM-P256-HKDF-SHA256/HKDF-SHA256/AES-128-GCM";

export type WorkloadEncryption = {
  suite: string;
  publicKey: string;
  keyId: string;
};

export type EncryptedStreamEnvelope = {
  v: number;
  sequence: number;
  ciphertext: string;
};

export type MediaAssetKind = "IMAGE" | "VIDEO" | "AUDIO";
export type MediaOperation = "METADATA" | "AUDIO_PURIFY";

export type MediaRequestHeader = {
  assetKind: MediaAssetKind;
  extension: string;
  mimeType: string;
  operation: MediaOperation;
};

export type MediaCleanResult = MediaRequestHeader & {
  v: number;
  requestId: string;
  inputBytes: number;
  outputBytes: number;
  changed: boolean;
  actions: string[];
  warning: string | null;
  creditsUsed: number;
  remainingCredits: number | null;
  guestCleansLeft: number | null;
  data: Uint8Array;
};

const MEDIA_REQUEST_MAGIC = encoder.encode("SUMM1");
const MEDIA_RESPONSE_MAGIC = encoder.encode("SUMR1");
const MEDIA_ENCRYPTED_MAGIC = encoder.encode("SUME1");

function encodeBase64url(value: Uint8Array) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < value.length; offset += chunkSize) {
    binary += String.fromCharCode(...value.subarray(offset, offset + chunkSize));
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeBase64url(value: string) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("Invalid base64url value.");
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(
    value.length + ((4 - value.length % 4) % 4),
    "=",
  );
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function joinBytes(...parts: Uint8Array[]) {
  const output = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function integerBytes(value: number, size: 2 | 4) {
  const output = new Uint8Array(size);
  const view = new DataView(output.buffer);
  if (size === 2) view.setUint16(0, value, false);
  else view.setUint32(0, value, false);
  return output;
}

function startsWith(value: Uint8Array, prefix: Uint8Array) {
  return prefix.every((byte, index) => value[index] === byte);
}

function requestAad(requestId: string) {
  return encoder.encode(`simpleunmark-request-v1\n${requestId}`);
}

function responseAad(requestId: string, sequence: number) {
  return encoder.encode(`simpleunmark-response-v1\n${requestId}\n${sequence}`);
}

export function sequenceNonce(baseNonce: Uint8Array, sequence: number) {
  if (baseNonce.length !== 12 || !Number.isSafeInteger(sequence) || sequence < 0) {
    throw new Error("Invalid encrypted stream sequence.");
  }
  const nonce = baseNonce.slice();
  let remaining = BigInt(sequence);
  for (let index = 11; index >= 4; index -= 1) {
    nonce[index] ^= Number(remaining & 0xffn);
    remaining >>= 8n;
  }
  return nonce;
}

async function sha256(value: Uint8Array) {
  const input = new ArrayBuffer(value.byteLength);
  new Uint8Array(input).set(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", input));
}

export function generateAttestationChallenge() {
  return encodeBase64url(crypto.getRandomValues(new Uint8Array(32)));
}

export async function attestationBinding(
  challenge: string,
  requestId: string,
  publicKey: string,
) {
  return encodeBase64url(await sha256(encoder.encode(
    `simpleunmark-attestation-v2\n${requestId}\n${challenge}\n${publicKey}`,
  )));
}

class ResponseDecryptor {
  private sequence = 0;
  private readonly requestId: string;
  private readonly key: CryptoKey;
  private readonly baseNonce: Uint8Array;

  constructor(
    requestId: string,
    key: CryptoKey,
    baseNonce: Uint8Array,
  ) {
    this.requestId = requestId;
    this.key = key;
    this.baseNonce = baseNonce;
  }

  async decrypt(envelope: EncryptedStreamEnvelope): Promise<CleanStreamEvent> {
    if (
      envelope.v !== 1
      || envelope.sequence !== this.sequence
      || !Number.isSafeInteger(envelope.sequence)
    ) {
      throw new Error("The encrypted response stream is out of order.");
    }
    const plaintext = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: sequenceNonce(this.baseNonce, envelope.sequence),
        additionalData: responseAad(this.requestId, envelope.sequence),
        tagLength: 128,
      },
      this.key,
      decodeBase64url(envelope.ciphertext),
    );
    this.sequence += 1;
    try {
      return JSON.parse(decoder.decode(plaintext)) as CleanStreamEvent;
    } catch {
      throw new Error("Malformed decrypted stream event");
    }
  }
}

class MediaResponseDecryptor {
  constructor(
    private readonly requestId: string,
    private readonly key: CryptoKey,
    private readonly baseNonce: Uint8Array,
    private readonly expected: MediaRequestHeader,
    private readonly inputBytes: number,
  ) {}

  async decrypt(ciphertext: ArrayBuffer): Promise<MediaCleanResult> {
    const plaintext = new Uint8Array(await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: sequenceNonce(this.baseNonce, 0),
        additionalData: responseAad(this.requestId, 0),
        tagLength: 128,
      },
      this.key,
      ciphertext,
    ));
    if (plaintext.length < 9 || !startsWith(plaintext, MEDIA_RESPONSE_MAGIC)) {
      throw new Error("The workload returned an invalid media response.");
    }
    const headerLength = new DataView(
      plaintext.buffer,
      plaintext.byteOffset + 5,
      4,
    ).getUint32(0, false);
    if (headerLength < 2 || headerLength > 32_000 || 9 + headerLength > plaintext.length) {
      throw new Error("The workload returned an invalid media response.");
    }
    let raw: Record<string, unknown>;
    try {
      const parsed = JSON.parse(decoder.decode(plaintext.subarray(9, 9 + headerLength))) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      raw = parsed as Record<string, unknown>;
    } catch {
      throw new Error("The workload returned invalid media result metadata.");
    }
    const data = plaintext.slice(9 + headerLength);
    const validNonnegativeInteger = (value: unknown): value is number =>
      typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
    const validNullableNumber = (value: unknown) => value === null
      || (typeof value === "number" && Number.isFinite(value) && value >= 0);
    const expectedExtension = this.expected.operation === "AUDIO_PURIFY"
      ? ".m4a"
      : this.expected.extension;
    const expectedMimeType = this.expected.operation === "AUDIO_PURIFY"
      ? "audio/mp4"
      : this.expected.mimeType;
    if (
      raw.v !== 1
      || raw.requestId !== this.requestId
      || raw.assetKind !== this.expected.assetKind
      || raw.operation !== this.expected.operation
      || raw.extension !== expectedExtension
      || raw.mimeType !== expectedMimeType
      || raw.inputBytes !== this.inputBytes
      || !validNonnegativeInteger(raw.outputBytes)
      || raw.outputBytes !== data.length
      || data.length === 0
      || typeof raw.changed !== "boolean"
      || !Array.isArray(raw.actions)
      || raw.actions.length > 30
      || raw.actions.some((action) => typeof action !== "string" || action.length > 300)
      || !(raw.warning === null || (typeof raw.warning === "string" && raw.warning.length <= 500))
      || typeof raw.creditsUsed !== "number"
      || !Number.isFinite(raw.creditsUsed)
      || raw.creditsUsed < 0
      || !validNullableNumber(raw.remainingCredits)
      || !(raw.guestCleansLeft === null
        || validNonnegativeInteger(raw.guestCleansLeft))
    ) {
      throw new Error("The workload returned invalid media result metadata.");
    }
    return { ...raw, data } as MediaCleanResult;
  }
}

export async function encryptCleanRequest(
  text: string,
  requestId: string,
  encryption: WorkloadEncryption,
) {
  if (encryption.suite !== CONFIDENTIAL_HPKE_SUITE) {
    throw new Error("The workload selected an unsupported encryption suite.");
  }
  const publicKeyBytes = decodeBase64url(encryption.publicKey);
  if (publicKeyBytes.length !== 65 || publicKeyBytes[0] !== 0x04) {
    throw new Error("The workload returned an invalid P-256 public key.");
  }
  const calculatedKeyId = `sha256:${Array.from(await sha256(publicKeyBytes), (byte) =>
    byte.toString(16).padStart(2, "0")).join("")}`;
  if (calculatedKeyId !== encryption.keyId) {
    throw new Error("The workload encryption key identifier does not match.");
  }

  const suite = new CipherSuite({
    kem: new DhkemP256HkdfSha256(),
    kdf: new HkdfSha256(),
    aead: new Aes128Gcm(),
  });
  const recipientPublicKey = await suite.kem.importKey(
    "raw",
    publicKeyBytes.buffer,
    true,
  );
  const context = await suite.createSenderContext({
    recipientPublicKey,
    info: HPKE_INFO,
  });
  const ciphertext = new Uint8Array(await context.seal(
    encoder.encode(JSON.stringify({ text })),
    requestAad(requestId),
  ));
  const responseKeyBytes = await context.export(RESPONSE_KEY_LABEL, 32);
  const responseNonce = new Uint8Array(await context.export(RESPONSE_NONCE_LABEL, 12));
  const responseKey = await crypto.subtle.importKey(
    "raw",
    responseKeyBytes,
    { name: "AES-GCM" },
    false,
    ["decrypt"],
  );

  return {
    body: {
      v: 1,
      enc: encodeBase64url(new Uint8Array(context.enc)),
      ciphertext: encodeBase64url(ciphertext),
    },
    responses: new ResponseDecryptor(requestId, responseKey, responseNonce),
  };
}

export async function encryptMediaRequest(
  data: Uint8Array,
  requestId: string,
  encryption: WorkloadEncryption,
  header: MediaRequestHeader,
) {
  if (encryption.suite !== CONFIDENTIAL_HPKE_SUITE) {
    throw new Error("The workload selected an unsupported encryption suite.");
  }
  const publicKeyBytes = decodeBase64url(encryption.publicKey);
  if (publicKeyBytes.length !== 65 || publicKeyBytes[0] !== 0x04) {
    throw new Error("The workload returned an invalid P-256 public key.");
  }
  const calculatedKeyId = `sha256:${Array.from(await sha256(publicKeyBytes), (byte) =>
    byte.toString(16).padStart(2, "0")).join("")}`;
  if (calculatedKeyId !== encryption.keyId) {
    throw new Error("The workload encryption key identifier does not match.");
  }

  const suite = new CipherSuite({
    kem: new DhkemP256HkdfSha256(),
    kdf: new HkdfSha256(),
    aead: new Aes128Gcm(),
  });
  const recipientPublicKey = await suite.kem.importKey("raw", publicKeyBytes.buffer, true);
  const context = await suite.createSenderContext({ recipientPublicKey, info: HPKE_INFO });
  const headerBytes = encoder.encode(JSON.stringify(header));
  const plaintext = joinBytes(
    MEDIA_REQUEST_MAGIC,
    integerBytes(headerBytes.length, 4),
    headerBytes,
    data,
  );
  const ciphertext = new Uint8Array(await context.seal(plaintext, requestAad(requestId)));
  const encapsulated = new Uint8Array(context.enc);
  const responseKey = await crypto.subtle.importKey(
    "raw",
    await context.export(RESPONSE_KEY_LABEL, 32),
    { name: "AES-GCM" },
    false,
    ["decrypt"],
  );
  const responseNonce = new Uint8Array(await context.export(RESPONSE_NONCE_LABEL, 12));

  return {
    body: joinBytes(
      MEDIA_ENCRYPTED_MAGIC,
      integerBytes(encapsulated.length, 2),
      encapsulated,
      ciphertext,
    ),
    response: new MediaResponseDecryptor(
      requestId,
      responseKey,
      responseNonce,
      header,
      data.length,
    ),
  };
}
