from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEFAULT_DEEPINFRA_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
DEFAULT_ATTESTATION_AUDIENCE = "https://simpleunmark.com/confidential-workload/v1"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _http_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{name} must be an absolute HTTP(S) URL")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    shared_secret: str = field(repr=False)
    receipt_url: str
    allowed_origins: tuple[str, ...]
    deepinfra_api_key: str = field(repr=False)
    deepinfra_base_url: str = DEFAULT_DEEPINFRA_BASE_URL
    deepinfra_model: str = DEFAULT_DEEPINFRA_MODEL
    attestation_audience: str = DEFAULT_ATTESTATION_AUDIENCE
    require_client_attestation: bool = False
    host: str = "0.0.0.0"  # noqa: S104 - the container must accept external traffic
    port: int = 8080
    log_level: str = "warning"
    revision: str | None = None
    max_body_bytes: int = 400_000
    max_media_bytes: int = 50 * 1024 * 1024
    provider_timeout_seconds: float = 90.0
    receipt_timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls, *, credentials: Mapping[str, str] | None = None) -> Settings:
        production = os.environ.get("REQUIRE_CLIENT_ATTESTATION") == "1"

        def credential(name: str) -> str:
            if credentials and name in credentials:
                return credentials[name]
            if production:
                raise RuntimeError(f"{name} must be loaded from Secret Manager")
            return _required(name)

        secret = credential("CONFIDENTIAL_SHARED_SECRET")
        if len(secret.encode()) < 32:
            raise RuntimeError("CONFIDENTIAL_SHARED_SECRET must be at least 32 bytes")

        origins = tuple(
            origin.strip().rstrip("/")
            for origin in _required("ALLOWED_ORIGINS").split(",")
            if origin.strip()
        )
        if not origins:
            raise RuntimeError("ALLOWED_ORIGINS must contain at least one origin")
        for origin in origins:
            _http_url("ALLOWED_ORIGINS", origin)

        return cls(
            shared_secret=secret,
            receipt_url=_http_url("APP_RECEIPT_URL", _required("APP_RECEIPT_URL")),
            allowed_origins=origins,
            deepinfra_api_key=credential("DEEPINFRA_API_KEY"),
            deepinfra_base_url=_http_url(
                "DEEPINFRA_BASE_URL",
                os.environ.get("DEEPINFRA_BASE_URL", DEFAULT_DEEPINFRA_BASE_URL),
            ),
            deepinfra_model=os.environ.get("DEEPINFRA_MODEL", DEFAULT_DEEPINFRA_MODEL),
            attestation_audience=os.environ.get(
                "ATTESTATION_AUDIENCE",
                DEFAULT_ATTESTATION_AUDIENCE,
            ),
            require_client_attestation=os.environ.get("REQUIRE_CLIENT_ATTESTATION") == "1",
            host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104
            port=int(os.environ.get("PORT", "8080")),
            log_level=os.environ.get("LOG_LEVEL", "warning"),
            revision=os.environ.get("SIMPLEUNMARK_REVISION") or None,
        )
