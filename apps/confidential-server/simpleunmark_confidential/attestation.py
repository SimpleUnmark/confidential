from __future__ import annotations

from typing import Protocol

import httpx

TEE_SERVER_SOCKET = "/run/container_launcher/teeserver.sock"


class AttestationError(RuntimeError):
    pass


class AttestationProvider(Protocol):
    async def token(self, nonce: str) -> str: ...


class DevelopmentAttestation:
    """Non-verifiable token used only when attestation checks are disabled."""

    async def token(self, nonce: str) -> str:
        return f"development.{nonce}.unverified"


class ConfidentialSpaceAttestation:
    def __init__(self, audience: str) -> None:
        self._audience = audience

    async def token(self, nonce: str) -> str:
        transport = httpx.AsyncHTTPTransport(uds=TEE_SERVER_SOCKET)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            timeout=15,
        ) as client:
            try:
                response = await client.post(
                    "/v1/token",
                    json={
                        "audience": self._audience,
                        "token_type": "OIDC",
                        "nonces": [nonce],
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise AttestationError("Confidential Space attestation is unavailable") from error
        token = response.text.strip().strip('"')
        if token.count(".") != 2:
            raise AttestationError("Confidential Space returned an invalid attestation token")
        return token
