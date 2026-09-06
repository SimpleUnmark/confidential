from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CAPABILITY_AUDIENCE = "simpleunmark-confidential-server"


class CapabilityError(ValueError):
    pass


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int
    aud: str
    requestId: str = Field(min_length=1, max_length=64)
    iat: int
    exp: int
    origin: str = Field(min_length=1, max_length=2_048)
    words: int = Field(ge=0, le=5_000)
    characters: int = Field(ge=0, le=60_000)
    inputBytes: int = Field(ge=1, le=50 * 1024 * 1024)
    mode: Literal["PRIVATE", "CONFIDENTIAL"]
    assetKind: Literal["TEXT", "IMAGE", "VIDEO", "AUDIO"] = "TEXT"
    fileExtension: str | None = Field(default=None, max_length=10)
    mimeType: str | None = Field(default=None, max_length=100)
    mediaOperation: Literal["METADATA", "AUDIO_PURIFY"] | None = None

    @model_validator(mode="after")
    def validate_asset_measurements(self) -> Capability:
        if self.assetKind == "TEXT":
            if self.words < 1 or self.characters < 1 or self.inputBytes > 256_000:
                raise ValueError("invalid text measurements")
            if (
                self.fileExtension is not None
                or self.mimeType is not None
                or self.mediaOperation is not None
            ):
                raise ValueError("text capabilities cannot include file metadata")
            return self

        if self.v != 2 or self.mode != "CONFIDENTIAL":
            raise ValueError("media requires a confidential v2 capability")
        if self.words != 0 or self.characters != 0:
            raise ValueError("media capabilities cannot include text measurements")
        if (
            self.fileExtension is None
            or not self.fileExtension.startswith(".")
            or not self.fileExtension[1:].isalnum()
            or self.fileExtension != self.fileExtension.lower()
        ):
            raise ValueError("invalid media extension")
        if (
            self.mimeType is None
            or any(ord(character) < 0x20 or ord(character) > 0x7E for character in self.mimeType)
        ):
            raise ValueError("invalid media type")
        if self.mediaOperation is None:
            raise ValueError("media operation is required")
        if self.mediaOperation == "AUDIO_PURIFY" and self.assetKind != "AUDIO":
            raise ValueError("audio purification requires audio")
        return self


def _decode_base64url(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError, binascii.Error) as error:
        raise CapabilityError("invalid capability encoding") from error


def verify_capability(token: str, secret: str, *, now: int | None = None) -> Capability:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
    except ValueError as error:
        raise CapabilityError("invalid capability format") from error

    expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    supplied = _decode_base64url(encoded_signature)
    if not hmac.compare_digest(expected, supplied):
        raise CapabilityError("invalid capability signature")

    try:
        raw = json.loads(_decode_base64url(encoded_payload))
        capability = Capability.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
        raise CapabilityError("invalid capability payload") from error

    current_time = int(time.time()) if now is None else now
    if capability.v not in {1, 2} or capability.aud != CAPABILITY_AUDIENCE:
        raise CapabilityError("unsupported capability")
    if capability.iat > current_time + 30:
        raise CapabilityError("capability is not active")
    if capability.exp <= current_time:
        raise CapabilityError("capability has expired")
    if capability.exp - capability.iat > 10 * 60:
        raise CapabilityError("capability lifetime is too long")
    return capability


def sign_bytes(payload: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
