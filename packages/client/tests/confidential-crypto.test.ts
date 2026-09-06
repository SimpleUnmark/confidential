import {
  Aes128Gcm,
  CipherSuite,
  DhkemP256HkdfSha256,
  HkdfSha256,
} from "@hpke/core";
import { describe, expect, it } from "vitest";
import {
  CONFIDENTIAL_HPKE_SUITE,
  attestationBinding,
  encryptCleanRequest,
  encryptMediaRequest,
  sequenceNonce,
} from "../src/confidential-crypto";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function encodeBase64url(value: Uint8Array) {
  return Buffer.from(value).toString("base64url");
}

function decodeBase64url(value: string) {
  return new Uint8Array(Buffer.from(value, "base64url"));
}

describe("confidential payload encryption", () => {
  it("encodes response sequence numbers as an eight-byte big-endian XOR", () => {
    const base = new Uint8Array(12);
    expect(Array.from(sequenceNonce(base, 255))).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 255]);
    expect(Array.from(sequenceNonce(base, 256))).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0]);
    expect(Array.from(sequenceNonce(base, 65_536))).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0]);
  });

  it("uses the same attestation key-binding frame as the Python workload", async () => {
    await expect(attestationBinding(
      "challenge-123456789012345678901234",
      "request-1",
      "public-key-value",
    )).resolves.toBe("YDQU527b6ab2T_vEISx_3K16mpIksePIdGeDJXMx-Aw");
  });

  it("encrypts a request and authenticates an exported-key response", async () => {
    const suite = new CipherSuite({
      kem: new DhkemP256HkdfSha256(),
      kdf: new HkdfSha256(),
      aead: new Aes128Gcm(),
    });
    const recipient = await suite.kem.generateKeyPair();
    const publicKey = new Uint8Array(await suite.kem.serializePublicKey(recipient.publicKey));
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", publicKey));
    const encrypted = await encryptCleanRequest("private text", "request-1", {
      suite: CONFIDENTIAL_HPKE_SUITE,
      publicKey: encodeBase64url(publicKey),
      keyId: `sha256:${Buffer.from(digest).toString("hex")}`,
    });

    const recipientContext = await suite.createRecipientContext({
      recipientKey: recipient,
      enc: decodeBase64url(encrypted.body.enc),
      info: encoder.encode("simpleunmark-hpke-v1"),
    });
    const plaintext = await recipientContext.open(
      decodeBase64url(encrypted.body.ciphertext),
      encoder.encode("simpleunmark-request-v1\nrequest-1"),
    );
    expect(JSON.parse(decoder.decode(plaintext))).toEqual({ text: "private text" });

    const responseKey = await crypto.subtle.importKey(
      "raw",
      await recipientContext.export(encoder.encode("simpleunmark-response-key-v1"), 32),
      { name: "AES-GCM" },
      false,
      ["encrypt"],
    );
    const responseNonce = new Uint8Array(
      await recipientContext.export(encoder.encode("simpleunmark-response-nonce-v1"), 12),
    );
    const event = { type: "delta" as const, text: "clean text" };
    const responseCiphertext = await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv: responseNonce,
        additionalData: encoder.encode("simpleunmark-response-v1\nrequest-1\n0"),
      },
      responseKey,
      encoder.encode(JSON.stringify(event)),
    );

    await expect(encrypted.responses.decrypt({
      v: 1,
      sequence: 0,
      ciphertext: encodeBase64url(new Uint8Array(responseCiphertext)),
    })).resolves.toEqual(event);
    await expect(encrypted.responses.decrypt({
      v: 1,
      sequence: 0,
      ciphertext: encodeBase64url(new Uint8Array(responseCiphertext)),
    })).rejects.toThrow("out of order");
  });

  it("round-trips binary media without base64 encoding the file", async () => {
    const suite = new CipherSuite({
      kem: new DhkemP256HkdfSha256(),
      kdf: new HkdfSha256(),
      aead: new Aes128Gcm(),
    });
    const recipient = await suite.kem.generateKeyPair();
    const publicKey = new Uint8Array(await suite.kem.serializePublicKey(recipient.publicKey));
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", publicKey));
    const source = Uint8Array.from([0, 1, 2, 253, 254, 255]);
    const encrypted = await encryptMediaRequest(source, "media-1", {
      suite: CONFIDENTIAL_HPKE_SUITE,
      publicKey: encodeBase64url(publicKey),
      keyId: `sha256:${Buffer.from(digest).toString("hex")}`,
    }, {
      assetKind: "IMAGE",
      extension: ".png",
      mimeType: "image/png",
      operation: "METADATA",
    });

    expect(decoder.decode(encrypted.body.subarray(0, 5))).toBe("SUME1");
    const encLength = new DataView(encrypted.body.buffer, encrypted.body.byteOffset + 5, 2)
      .getUint16(0, false);
    const recipientContext = await suite.createRecipientContext({
      recipientKey: recipient,
      enc: encrypted.body.slice(7, 7 + encLength),
      info: encoder.encode("simpleunmark-hpke-v1"),
    });
    const plaintext = new Uint8Array(await recipientContext.open(
      encrypted.body.slice(7 + encLength),
      encoder.encode("simpleunmark-request-v1\nmedia-1"),
    ));
    expect(decoder.decode(plaintext.subarray(0, 5))).toBe("SUMM1");
    expect(Array.from(plaintext.slice(-source.length))).toEqual(Array.from(source));

    const output = Uint8Array.from([9, 8, 7]);
    const resultHeader = encoder.encode(JSON.stringify({
      v: 1,
      requestId: "media-1",
      assetKind: "IMAGE",
      operation: "METADATA",
      extension: ".png",
      mimeType: "image/png",
      inputBytes: source.length,
      outputBytes: output.length,
      changed: true,
      actions: ["drop PNG tEXt chunk"],
      warning: null,
      creditsUsed: 0.2,
      remainingCredits: 9.8,
      guestCleansLeft: null,
    }));
    const responseFrame = new Uint8Array(9 + resultHeader.length + output.length);
    responseFrame.set(encoder.encode("SUMR1"), 0);
    new DataView(responseFrame.buffer).setUint32(5, resultHeader.length, false);
    responseFrame.set(resultHeader, 9);
    responseFrame.set(output, 9 + resultHeader.length);
    const responseKey = await crypto.subtle.importKey(
      "raw",
      await recipientContext.export(encoder.encode("simpleunmark-response-key-v1"), 32),
      { name: "AES-GCM" },
      false,
      ["encrypt"],
    );
    const responseNonce = new Uint8Array(
      await recipientContext.export(encoder.encode("simpleunmark-response-nonce-v1"), 12),
    );
    const ciphertext = await crypto.subtle.encrypt({
      name: "AES-GCM",
      iv: responseNonce,
      additionalData: encoder.encode("simpleunmark-response-v1\nmedia-1\n0"),
    }, responseKey, responseFrame);

    const result = await encrypted.response.decrypt(ciphertext);
    expect(result.actions).toEqual(["drop PNG tEXt chunk"]);
    expect(Array.from(result.data)).toEqual(Array.from(output));
  });
});
