from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pyhpke import AEADId, CipherSuite, KDFId, KEMId
from pyhpke.kem_key_interface import KEMKeyInterface

HPKE_INFO = b"simpleunmark-hpke-v1"
HPKE_SUITE_NAME = "DHKEM-P256-HKDF-SHA256/HKDF-SHA256/AES-128-GCM"
RESPONSE_KEY_LABEL = b"simpleunmark-response-key-v1"
RESPONSE_NONCE_LABEL = b"simpleunmark-response-nonce-v1"
BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PayloadEncryptionError(ValueError):
    pass


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_base64url(value: str) -> bytes:
    if not BASE64URL_PATTERN.fullmatch(value):
        raise PayloadEncryptionError("invalid base64url value")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise PayloadEncryptionError("invalid base64url value") from error


def attestation_binding(*, challenge: str, request_id: str, public_key: str) -> str:
    framed = (
        "simpleunmark-attestation-v2\n"
        f"{request_id}\n"
        f"{challenge}\n"
        f"{public_key}"
    ).encode()
    return encode_base64url(hashlib.sha256(framed).digest())


def request_aad(request_id: str) -> bytes:
    return f"simpleunmark-request-v1\n{request_id}".encode()


def response_aad(request_id: str, sequence: int) -> bytes:
    return f"simpleunmark-response-v1\n{request_id}\n{sequence}".encode()


def _sequence_nonce(base_nonce: bytes, sequence: int) -> bytes:
    if len(base_nonce) != 12 or not 0 <= sequence < 2**64:
        raise PayloadEncryptionError("invalid response nonce state")
    nonce = bytearray(base_nonce)
    encoded_sequence = sequence.to_bytes(8, "big")
    for index, value in enumerate(encoded_sequence, start=4):
        nonce[index] ^= value
    return bytes(nonce)


@dataclass(slots=True)
class ResponseCipher:
    request_id: str
    key: bytes
    base_nonce: bytes
    sequence: int = 0

    def encrypt_payload(self, plaintext: bytes) -> bytes:
        sequence = self.sequence
        ciphertext = AESGCM(self.key).encrypt(
            _sequence_nonce(self.base_nonce, sequence),
            plaintext,
            response_aad(self.request_id, sequence),
        )
        self.sequence += 1
        return ciphertext

    def encrypt_event(self, event: dict[str, Any]) -> bytes:
        plaintext = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        sequence = self.sequence
        ciphertext = self.encrypt_payload(plaintext)
        envelope = {
            "v": 1,
            "sequence": sequence,
            "ciphertext": encode_base64url(ciphertext),
        }
        return (json.dumps(envelope, separators=(",", ":")) + "\n").encode()


class WorkloadEncryptionKey:
    """An HPKE recipient key that exists only in this workload process."""

    def __init__(self) -> None:
        suite = CipherSuite.new(
            KEMId.DHKEM_P256_HKDF_SHA256,
            KDFId.HKDF_SHA256,
            AEADId.AES128_GCM,
        )
        key_pair = suite.kem.derive_key_pair(secrets.token_bytes(32))
        self._suite = suite
        self._private_key: KEMKeyInterface = key_pair.private_key
        public_key_bytes = key_pair.public_key.to_public_bytes()
        self.public_key = encode_base64url(public_key_bytes)
        self.key_id = f"sha256:{hashlib.sha256(public_key_bytes).hexdigest()}"

    def decrypt_request(
        self,
        *,
        request_id: str,
        encapsulated_key: str,
        ciphertext: str,
    ) -> tuple[bytes, ResponseCipher]:
        return self.decrypt_request_bytes(
            request_id=request_id,
            encapsulated_key=decode_base64url(encapsulated_key),
            ciphertext=decode_base64url(ciphertext),
        )

    def decrypt_request_bytes(
        self,
        *,
        request_id: str,
        encapsulated_key: bytes,
        ciphertext: bytes,
    ) -> tuple[bytes, ResponseCipher]:
        try:
            context = self._suite.create_recipient_context(
                encapsulated_key,
                self._private_key,
                info=HPKE_INFO,
            )
            plaintext = context.open(
                ciphertext,
                aad=request_aad(request_id),
            )
            response_key = context.export(RESPONSE_KEY_LABEL, 32)
            response_nonce = context.export(RESPONSE_NONCE_LABEL, 12)
        except Exception as error:
            raise PayloadEncryptionError("encrypted request could not be opened") from error
        return plaintext, ResponseCipher(request_id, response_key, response_nonce)
