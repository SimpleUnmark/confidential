from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pyhpke import AEADId, CipherSuite, KDFId, KEMId

import simpleunmark_confidential.app as app_module
from simpleunmark_confidential.app import create_app
from simpleunmark_confidential.config import Settings
from simpleunmark_confidential.crypto import (
    HPKE_INFO,
    RESPONSE_KEY_LABEL,
    RESPONSE_NONCE_LABEL,
    attestation_binding,
    decode_base64url,
    encode_base64url,
    request_aad,
    response_aad,
)
from simpleunmark_confidential.media import MEDIA_REQUEST_MAGIC, MEDIA_RESPONSE_MAGIC
from simpleunmark_confidential.provider import ProviderDelta, ProviderEvent, ProviderResult
from simpleunmark_confidential.receipts import ReceiptResult
from simpleunmark_confidential.text import count_words

SECRET = "test-secret-that-is-at-least-32-bytes"
ORIGIN = "https://simpleunmark.com"


class FakeProvider:
    async def stream(self, text: str) -> AsyncIterator[ProviderEvent]:
        assert "\u200b" not in text
        yield ProviderDelta("Clean ")
        yield ProviderDelta("result")
        yield ProviderResult(
            text="Clean result",
            model="test-model",
            service_tier=None,
            request_id="provider-1",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
            cost_micros=5,
            finish_reason="stop",
            provider_ms=3,
            attempts=1,
        )


class EmptyProvider:
    async def stream(self, text: str) -> AsyncIterator[ProviderEvent]:
        if False:
            yield ProviderDelta(text)


class FakeReceipts:
    def __init__(self) -> None:
        self.values: list[dict[str, object]] = []

    async def submit(
        self, receipt: dict[str, object], *, attempts: int = 3
    ) -> ReceiptResult:
        self.values.append(receipt)
        return ReceiptResult(remaining_credits=9.9, guest_cleans_left=None, credits_used=0.1)


class FakeAttestation:
    def __init__(self) -> None:
        self.nonces: list[str] = []

    async def token(self, nonce: str) -> str:
        self.nonces.append(nonce)
        return f"header.{nonce}.signature"


class SlowFakeAttestation(FakeAttestation):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def token(self, nonce: str) -> str:
        self.nonces.append(nonce)
        self.started.set()
        await self.release.wait()
        return f"header.{nonce}.signature"


def _settings() -> Settings:
    return Settings(
        shared_secret=SECRET,
        receipt_url="https://app.example/api/clean/receipt",
        allowed_origins=(ORIGIN,),
        deepinfra_api_key="provider-secret",
    )


def _capability(
    text: str,
    mode: str = "CONFIDENTIAL",
    request_id: str = "request-1",
) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "aud": "simpleunmark-confidential-server",
        "requestId": request_id,
        "iat": now,
        "exp": now + 300,
        "origin": ORIGIN,
        "words": count_words(text),
        "characters": len(text),
        "inputBytes": len(text.encode()),
        "mode": mode,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _media_capability(data: bytes, request_id: str = "media-request-1") -> str:
    now = int(time.time())
    payload = {
        "v": 2,
        "aud": "simpleunmark-confidential-server",
        "requestId": request_id,
        "iat": now,
        "exp": now + 300,
        "origin": ORIGIN,
        "words": 0,
        "characters": 0,
        "inputBytes": len(data),
        "mode": "CONFIDENTIAL",
        "assetKind": "IMAGE",
        "fileExtension": ".png",
        "mimeType": "image/png",
        "mediaOperation": "METADATA",
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _response_nonce(base_nonce: bytes, sequence: int) -> bytes:
    nonce = bytearray(base_nonce)
    for index, value in enumerate(sequence.to_bytes(8, "big"), start=4):
        nonce[index] ^= value
    return bytes(nonce)


class ResponseDecryptor:
    def __init__(self, request_id: str, key: bytes, nonce: bytes) -> None:
        self._request_id = request_id
        self._key = key
        self._nonce = nonce

    def decrypt(self, response: httpx.Response) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for expected_sequence, line in enumerate(response.text.splitlines()):
            envelope = json.loads(line)
            assert envelope["v"] == 1
            assert envelope["sequence"] == expected_sequence
            plaintext = AESGCM(self._key).decrypt(
                _response_nonce(self._nonce, expected_sequence),
                decode_base64url(envelope["ciphertext"]),
                response_aad(self._request_id, expected_sequence),
            )
            events.append(json.loads(plaintext))
        return events

    def decrypt_bytes(self, ciphertext: bytes) -> bytes:
        return AESGCM(self._key).decrypt(
            _response_nonce(self._nonce, 0),
            ciphertext,
            response_aad(self._request_id, 0),
        )


async def _attest_and_encrypt(
    client: httpx.AsyncClient,
    token: str,
    text: str,
    request_id: str = "request-1",
) -> tuple[dict[str, str], dict[str, object], ResponseDecryptor, dict[str, str]]:
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}
    attested = await client.post(
        "/v1/attestation",
        headers=headers,
        json={"challenge": "test-browser-challenge-value-1234567890"},
    )
    assert attested.status_code == 200
    encryption = attested.json()["encryption"]
    suite = CipherSuite.new(
        KEMId.DHKEM_P256_HKDF_SHA256,
        KDFId.HKDF_SHA256,
        AEADId.AES128_GCM,
    )
    public_key = suite.kem.deserialize_public_key(
        decode_base64url(encryption["publicKey"])
    )
    encapsulated_key, context = suite.create_sender_context(public_key, info=HPKE_INFO)
    ciphertext = context.seal(
        json.dumps({"text": text}, separators=(",", ":")).encode(),
        aad=request_aad(request_id),
    )
    decryptor = ResponseDecryptor(
        request_id,
        context.export(RESPONSE_KEY_LABEL, 32),
        context.export(RESPONSE_NONCE_LABEL, 12),
    )
    return (
        headers,
        {
            "v": 1,
            "enc": encode_base64url(encapsulated_key),
            "ciphertext": encode_base64url(ciphertext),
        },
        decryptor,
        encryption,
    )


async def _attest_and_encrypt_media(
    client: httpx.AsyncClient,
    token: str,
    data: bytes,
    request_id: str = "media-request-1",
) -> tuple[dict[str, str], bytes, ResponseDecryptor]:
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}
    attested = await client.post(
        "/v1/attestation",
        headers=headers,
        json={"challenge": "test-browser-challenge-value-1234567890"},
    )
    assert attested.status_code == 200
    suite = CipherSuite.new(
        KEMId.DHKEM_P256_HKDF_SHA256,
        KDFId.HKDF_SHA256,
        AEADId.AES128_GCM,
    )
    public_key = suite.kem.deserialize_public_key(
        decode_base64url(attested.json()["encryption"]["publicKey"])
    )
    encapsulated_key, context = suite.create_sender_context(public_key, info=HPKE_INFO)
    header = json.dumps(
        {
            "assetKind": "IMAGE",
            "extension": ".png",
            "mimeType": "image/png",
            "operation": "METADATA",
        },
        separators=(",", ":"),
    ).encode()
    plaintext = MEDIA_REQUEST_MAGIC + struct.pack(">I", len(header)) + header + data
    ciphertext = context.seal(plaintext, aad=request_aad(request_id))
    body = b"SUME1" + struct.pack(">H", len(encapsulated_key)) + encapsulated_key + ciphertext
    decryptor = ResponseDecryptor(
        request_id,
        context.export(RESPONSE_KEY_LABEL, 32),
        context.export(RESPONSE_NONCE_LABEL, 12),
    )
    return headers, body, decryptor


@pytest.mark.asyncio
async def test_info_preserves_boolean_attestation_status() -> None:
    app = create_app(_settings(), provider=FakeProvider(), receipts=FakeReceipts())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        response = await client.get("/v1/info")

    assert response.status_code == 200
    assert response.json()["clientAttestationRequired"] is False
    assert response.json()["cleanerVersion"] == "v0.7.0"
    assert response.json()["media"]["operations"] == ["metadata", "audio-purify"]


@pytest.mark.asyncio
async def test_streams_content_directly_and_sends_only_content_free_receipts() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Secret\u200b message"
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, decryptor, _ = await _attest_and_encrypt(
            client,
            _capability(text),
            text,
        )
        response = await client.post(
            "/v1/clean",
            headers=headers,
            json=encrypted,
        )

    assert response.status_code == 200
    assert "Secret" not in json.dumps(encrypted)
    assert "Clean result" not in response.text
    events = decryptor.decrypt(response)
    assert events[-1]["result"]["cleanedText"] == "Clean result"
    assert [receipt["status"] for receipt in receipts.values] == ["started", "succeeded"]
    assert all("text" not in receipt for receipt in receipts.values)
    assert all(receipt["mode"] == "CONFIDENTIAL" for receipt in receipts.values)


@pytest.mark.asyncio
async def test_cleans_encrypted_media_and_returns_only_encrypted_file_bytes() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
        "AScY42YAAAAASUVORK5CYII="
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, decryptor = await _attest_and_encrypt_media(
            client,
            _media_capability(png),
            png,
        )
        response = await client.post(
            "/v1/media/clean",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=encrypted,
        )

    assert response.status_code == 200
    assert png not in response.content
    plaintext = decryptor.decrypt_bytes(response.content)
    assert plaintext.startswith(MEDIA_RESPONSE_MAGIC)
    header_size = struct.unpack(">I", plaintext[5:9])[0]
    result = json.loads(plaintext[9 : 9 + header_size])
    assert result["assetKind"] == "IMAGE"
    assert result["operation"] == "METADATA"
    assert result["outputBytes"] == len(plaintext[9 + header_size :])
    assert [receipt["status"] for receipt in receipts.values] == ["started", "succeeded"]
    assert all(receipt["assetKind"] == "IMAGE" for receipt in receipts.values)
    assert all(receipt["mediaOperation"] == "METADATA" for receipt in receipts.values)


@pytest.mark.asyncio
async def test_rejects_a_concurrent_media_job_before_claiming_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipts = FakeReceipts()
    real_clean_media = app_module.clean_media
    started = threading.Event()
    release = threading.Event()

    def slow_clean_media(media: object) -> object:
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError
        return real_clean_media(media)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "clean_media", slow_clean_media)
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
        "AScY42YAAAAASUVORK5CYII="
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        first_headers, first_encrypted, _ = await _attest_and_encrypt_media(
            client,
            _media_capability(png, "media-request-1"),
            png,
            "media-request-1",
        )
        second_headers, second_encrypted, _ = await _attest_and_encrypt_media(
            client,
            _media_capability(png, "media-request-2"),
            png,
            "media-request-2",
        )
        first_task = asyncio.create_task(
            client.post(
                "/v1/media/clean",
                headers={**first_headers, "Content-Type": "application/octet-stream"},
                content=first_encrypted,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        try:
            busy = await client.post(
                "/v1/media/clean",
                headers={**second_headers, "Content-Type": "application/octet-stream"},
                content=second_encrypted,
            )
        finally:
            release.set()
        first = await first_task

    assert first.status_code == 200
    assert busy.status_code == 429
    assert busy.json()["code"] == "BUSY"
    assert [receipt["requestId"] for receipt in receipts.values] == [
        "media-request-1",
        "media-request-1",
    ]


@pytest.mark.asyncio
async def test_private_endpoint_streams_plain_events_and_tracks_the_mode() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Private\u200b message"
    headers = {
        "Authorization": f"Bearer {_capability(text, 'PRIVATE')}",
        "Origin": ORIGIN,
    }
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        response = await client.post("/v1/plain/clean", headers=headers, json={"text": text})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["result"]["cleanedText"] == "Clean result"
    assert events[-1]["result"]["privacyMode"] == "PRIVATE"
    assert [receipt["status"] for receipt in receipts.values] == ["started", "succeeded"]
    assert all(receipt["mode"] == "PRIVATE" for receipt in receipts.values)
    telemetry = receipts.values[-1]["telemetry"]
    assert isinstance(telemetry, dict)
    assert telemetry["totalTokens"] == 12
    assert telemetry["providerCostMicros"] == 5


@pytest.mark.asyncio
async def test_mode_binding_prevents_endpoint_downgrades() -> None:
    app = create_app(_settings(), provider=FakeProvider(), receipts=FakeReceipts())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Expected text"
    headers = {
        "Authorization": f"Bearer {_capability(text, 'PRIVATE')}",
        "Origin": ORIGIN,
    }
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        attestation = await client.post(
            "/v1/attestation",
            headers=headers,
            json={"challenge": "test-browser-challenge-value-1234567890"},
        )

    assert attestation.status_code == 403


@pytest.mark.asyncio
async def test_rejects_text_that_does_not_match_the_signed_measurements() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, _, _ = await _attest_and_encrypt(
            client,
            _capability("Expected text"),
            "Different text",
        )
        response = await client.post(
            "/v1/clean",
            headers=headers,
            json=encrypted,
        )

    assert response.status_code == 403
    assert receipts.values == []


@pytest.mark.asyncio
async def test_requires_nonce_bound_attestation_when_enabled() -> None:
    text = "Expected text"
    token = _capability(text)
    settings = replace(_settings(), require_client_attestation=True)
    fake_attestation = FakeAttestation()
    app = create_app(
        settings,
        provider=FakeProvider(),
        receipts=FakeReceipts(),  # type: ignore[arg-type]
        attestation=fake_attestation,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        rejected = await client.post(
            "/v1/clean",
            headers=headers,
            json={"v": 1, "enc": "A" * 87, "ciphertext": "A" * 24},
        )
        encrypted_headers, encrypted, decryptor, encryption = await _attest_and_encrypt(
            client,
            token,
            text,
        )
        accepted = await client.post("/v1/clean", headers=encrypted_headers, json=encrypted)
        replayed = await client.post("/v1/clean", headers=encrypted_headers, json=encrypted)

    assert rejected.status_code == 428
    assert accepted.status_code == 200
    assert replayed.status_code == 428
    assert decryptor.decrypt(accepted)[-1]["type"] == "complete"
    assert len(fake_attestation.nonces) == 1
    assert fake_attestation.nonces[0] == attestation_binding(
        challenge="test-browser-challenge-value-1234567890",
        request_id="request-1",
        public_key=encryption["publicKey"],
    )


@pytest.mark.asyncio
async def test_attestation_keys_are_scoped_to_one_request() -> None:
    app = create_app(_settings(), provider=FakeProvider(), receipts=FakeReceipts())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    challenge = "test-browser-challenge-value-1234567890"
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        responses = []
        for request_id in ("request-1", "request-2"):
            responses.append(await client.post(
                "/v1/attestation",
                headers={
                    "Authorization": (
                        f"Bearer {_capability('Expected text', request_id=request_id)}"
                    ),
                    "Origin": ORIGIN,
                },
                json={"challenge": challenge},
            ))
        repeated = await client.post(
            "/v1/attestation",
            headers={
                "Authorization": f"Bearer {_capability('Expected text')}",
                "Origin": ORIGIN,
            },
            json={"challenge": challenge},
        )
        conflicting = await client.post(
            "/v1/attestation",
            headers={
                "Authorization": f"Bearer {_capability('Expected text')}",
                "Origin": ORIGIN,
            },
            json={"challenge": "different-browser-challenge-1234567890"},
        )

    assert all(response.status_code == 200 for response in responses)
    first_key = responses[0].json()["encryption"]["publicKey"]
    second_key = responses[1].json()["encryption"]["publicKey"]
    assert first_key != second_key
    assert repeated.json()["encryption"]["publicKey"] == first_key
    assert conflicting.status_code == 409


@pytest.mark.asyncio
async def test_concurrent_attestation_retries_share_one_token_mint() -> None:
    text = "Expected text"
    fake_attestation = SlowFakeAttestation()
    app = create_app(
        replace(_settings(), require_client_attestation=True),
        provider=FakeProvider(),
        receipts=FakeReceipts(),  # type: ignore[arg-type]
        attestation=fake_attestation,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": f"Bearer {_capability(text)}",
        "Origin": ORIGIN,
    }
    body = {"challenge": "test-browser-challenge-value-1234567890"}
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        first = asyncio.create_task(client.post("/v1/attestation", headers=headers, json=body))
        await asyncio.wait_for(fake_attestation.started.wait(), timeout=1)
        second = asyncio.create_task(client.post("/v1/attestation", headers=headers, json=body))
        await asyncio.sleep(0)
        fake_attestation.release.set()
        responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [200, 200]
    assert len(fake_attestation.nonces) == 1
    assert responses[0].json() == responses[1].json()


@pytest.mark.asyncio
async def test_concurrent_attestation_with_a_different_challenge_is_rejected() -> None:
    text = "Expected text"
    fake_attestation = SlowFakeAttestation()
    app = create_app(
        replace(_settings(), require_client_attestation=True),
        provider=FakeProvider(),
        receipts=FakeReceipts(),  # type: ignore[arg-type]
        attestation=fake_attestation,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": f"Bearer {_capability(text)}",
        "Origin": ORIGIN,
    }
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        first = asyncio.create_task(
            client.post(
                "/v1/attestation",
                headers=headers,
                json={"challenge": "test-browser-challenge-value-1234567890"},
            )
        )
        await asyncio.wait_for(fake_attestation.started.wait(), timeout=1)
        conflicting = await client.post(
            "/v1/attestation",
            headers=headers,
            json={"challenge": "different-browser-challenge-1234567890"},
        )
        fake_attestation.release.set()
        accepted = await first

    assert accepted.status_code == 200
    assert conflicting.status_code == 409
    assert len(fake_attestation.nonces) == 1


@pytest.mark.asyncio
async def test_failed_decrypt_keeps_the_request_key_for_a_valid_retry() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=FakeProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Expected text"
    token = _capability(text)
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, decryptor, _ = await _attest_and_encrypt(client, token, text)
        invalid = await client.post(
            "/v1/clean",
            headers=headers,
            json={**encrypted, "ciphertext": "A" * 24},
        )
        accepted = await client.post("/v1/clean", headers=headers, json=encrypted)

    assert invalid.status_code == 400
    assert accepted.status_code == 200
    assert decryptor.decrypt(accepted)[-1]["type"] == "complete"


@pytest.mark.asyncio
async def test_sixth_failed_decrypt_drops_the_request_key() -> None:
    app = create_app(_settings(), provider=FakeProvider(), receipts=FakeReceipts())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Expected text"
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, _, _ = await _attest_and_encrypt(
            client,
            _capability(text),
            text,
        )
        responses = [
            await client.post(
                "/v1/clean",
                headers=headers,
                json={**encrypted, "ciphertext": "A" * 24},
            )
            for _ in range(6)
        ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400, 400, 428]


@pytest.mark.asyncio
async def test_provider_empty_result_degrades_to_deterministic_cleanup_and_refunds() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=EmptyProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Secret\u200b message"
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, decryptor, _ = await _attest_and_encrypt(
            client,
            _capability(text),
            text,
        )
        response = await client.post("/v1/clean", headers=headers, json=encrypted)

    events = decryptor.decrypt(response)
    assert response.status_code == 200
    assert [receipt["status"] for receipt in receipts.values] == ["started", "failed"]
    assert events[-1]["type"] == "complete"
    assert events[-1]["result"]["cleanedText"] == "Secret message"
    assert events[-1]["result"]["strongApplied"] is False
    assert events[-1]["result"]["creditsUsed"] == 0
    assert "credits were returned" in events[-1]["result"]["warning"]


@pytest.mark.asyncio
async def test_provider_empty_result_is_an_error_when_cleanup_changes_nothing() -> None:
    receipts = FakeReceipts()
    app = create_app(_settings(), provider=EmptyProvider(), receipts=receipts)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    text = "Already ordinary text"
    async with httpx.AsyncClient(transport=transport, base_url="https://tee.example") as client:
        headers, encrypted, decryptor, _ = await _attest_and_encrypt(
            client,
            _capability(text),
            text,
        )
        response = await client.post("/v1/clean", headers=headers, json=encrypted)

    events = decryptor.decrypt(response)
    assert [receipt["status"] for receipt in receipts.values] == ["started", "failed"]
    assert events[-1]["type"] == "error"
    assert "credits were returned" in events[-1]["error"]["error"]
