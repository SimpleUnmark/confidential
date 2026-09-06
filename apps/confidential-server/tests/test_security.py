from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from simpleunmark_confidential.security import CapabilityError, verify_capability

SECRET = "x" * 32


def _token(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{encoded}.{signature}"


def _payload() -> dict[str, object]:
    return {
        "v": 1,
        "aud": "simpleunmark-confidential-server",
        "requestId": "request-1",
        "iat": 100,
        "exp": 400,
        "origin": "https://simpleunmark.com",
        "words": 2,
        "characters": 11,
        "inputBytes": 11,
        "mode": "CONFIDENTIAL",
    }


def test_verifies_a_short_lived_capability() -> None:
    capability = verify_capability(_token(_payload()), SECRET, now=200)
    assert capability.requestId == "request-1"


def test_rejects_tampering_and_expiration() -> None:
    token = _token(_payload())
    encoded, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    with pytest.raises(CapabilityError):
        verify_capability(f"{encoded}.{replacement}{signature[1:]}", SECRET, now=200)
    with pytest.raises(CapabilityError):
        verify_capability(token, SECRET, now=400)


@pytest.mark.parametrize(
    ("changes", "now"),
    [
        ({"v": 3}, 200),
        ({"aud": "another-service"}, 200),
        ({"iat": 231}, 200),
        ({"iat": 100, "exp": 701}, 200),
    ],
)
def test_rejects_capabilities_outside_the_protocol(
    changes: dict[str, object],
    now: int,
) -> None:
    payload = {**_payload(), **changes}
    with pytest.raises(CapabilityError):
        verify_capability(_token(payload), SECRET, now=now)


def test_rejects_non_base64url_encoding() -> None:
    token = _token(_payload())
    encoded, _ = token.split(".")
    with pytest.raises(CapabilityError):
        verify_capability(f"{encoded}.not+base64", SECRET, now=200)


def test_rejects_a_zero_word_capability() -> None:
    payload = {**_payload(), "words": 0, "characters": 1, "inputBytes": 4}

    with pytest.raises(CapabilityError):
        verify_capability(_token(payload), SECRET, now=200)


def test_verifies_a_media_capability_without_text_measurements() -> None:
    payload = {
        **_payload(),
        "v": 2,
        "words": 0,
        "characters": 0,
        "inputBytes": 1_024,
        "assetKind": "IMAGE",
        "fileExtension": ".png",
        "mimeType": "image/png",
        "mediaOperation": "METADATA",
    }

    capability = verify_capability(_token(payload), SECRET, now=200)

    assert capability.assetKind == "IMAGE"
    assert capability.fileExtension == ".png"
    assert capability.mediaOperation == "METADATA"


def test_rejects_media_on_the_plaintext_endpoint_mode() -> None:
    payload = {
        **_payload(),
        "v": 2,
        "words": 0,
        "characters": 0,
        "inputBytes": 1_024,
        "mode": "PRIVATE",
        "assetKind": "VIDEO",
        "fileExtension": ".mp4",
        "mimeType": "video/mp4",
        "mediaOperation": "METADATA",
    }

    with pytest.raises(CapabilityError):
        verify_capability(_token(payload), SECRET, now=200)


def test_rejects_audio_purification_for_non_audio_media() -> None:
    payload = {
        **_payload(),
        "v": 2,
        "words": 0,
        "characters": 0,
        "inputBytes": 1_024,
        "assetKind": "IMAGE",
        "fileExtension": ".png",
        "mimeType": "image/png",
        "mediaOperation": "AUDIO_PURIFY",
    }

    with pytest.raises(CapabilityError):
        verify_capability(_token(payload), SECRET, now=200)
