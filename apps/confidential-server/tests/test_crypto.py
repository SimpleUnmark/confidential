from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pyhpke import AEADId, CipherSuite, KDFId, KEMId

from simpleunmark_confidential.crypto import (
    HPKE_INFO,
    RESPONSE_KEY_LABEL,
    RESPONSE_NONCE_LABEL,
    PayloadEncryptionError,
    WorkloadEncryptionKey,
    attestation_binding,
    decode_base64url,
    encode_base64url,
    request_aad,
    response_aad,
)


def test_attestation_binding_is_stable_across_browser_and_workload() -> None:
    assert attestation_binding(
        challenge="challenge-123456789012345678901234",
        request_id="request-1",
        public_key="public-key-value",
    ) == "YDQU527b6ab2T_vEISx_3K16mpIksePIdGeDJXMx-Aw"


def test_hpke_request_and_exported_response_keys_interoperate() -> None:
    workload = WorkloadEncryptionKey()
    suite = CipherSuite.new(
        KEMId.DHKEM_P256_HKDF_SHA256,
        KDFId.HKDF_SHA256,
        AEADId.AES128_GCM,
    )
    public_key = suite.kem.deserialize_public_key(decode_base64url(workload.public_key))
    encapsulated_key, sender = suite.create_sender_context(public_key, info=HPKE_INFO)
    ciphertext = sender.seal(b'{"text":"private"}', aad=request_aad("request-1"))

    plaintext, responses = workload.decrypt_request(
        request_id="request-1",
        encapsulated_key=encode_base64url(encapsulated_key),
        ciphertext=encode_base64url(ciphertext),
    )
    assert plaintext == b'{"text":"private"}'

    line = responses.encrypt_event({"type": "delta", "text": "clean"})
    envelope = json.loads(line)
    decrypted = AESGCM(sender.export(RESPONSE_KEY_LABEL, 32)).decrypt(
        sender.export(RESPONSE_NONCE_LABEL, 12),
        decode_base64url(envelope["ciphertext"]),
        response_aad("request-1", 0),
    )
    assert json.loads(decrypted) == {"type": "delta", "text": "clean"}

    tampered = bytearray(ciphertext)
    tampered[-1] ^= 1
    with pytest.raises(PayloadEncryptionError):
        workload.decrypt_request(
            request_id="request-1",
            encapsulated_key=encode_base64url(encapsulated_key),
            ciphertext=encode_base64url(bytes(tampered)),
        )
