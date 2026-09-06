from __future__ import annotations

import base64
import os
import re

import google_crc32c
import httpx

METADATA_ROOT = "http://metadata.google.internal/computeMetadata/v1"
API_ROOT = "https://secretmanager.googleapis.com/v1"
SECRET_REFERENCES = {
    "simpleunmark-deepinfra-secret-version": "DEEPINFRA_API_KEY",
    "simpleunmark-shared-secret-version": "CONFIDENTIAL_SHARED_SECRET",
}
VERSION_PATTERN = re.compile(
    r"projects/(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]+)/secrets/"
    r"[A-Za-z0-9_-]{1,255}/versions/[1-9][0-9]*"
)


def bootstrap_credentials() -> dict[str, str]:
    """Fetch production credentials without writing them to environment or disk.

    Production always uses Secret Manager, even if credential environment
    variables exist. Local development uses environment variables via Settings.
    This is service-account IAM authentication, not attestation-gated release.
    Never fall back to the obsolete plaintext metadata attributes.
    """
    if os.environ.get("REQUIRE_CLIENT_ATTESTATION") != "1":
        return {}

    try:
        # No proxy inheritance or redirects: metadata access tokens must only go
        # to the fixed Google Secret Manager host, never metadata-supplied URLs.
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            references: dict[str, str] = {}
            for attribute, environment_name in SECRET_REFERENCES.items():
                response = client.get(
                    f"{METADATA_ROOT}/instance/attributes/{attribute}",
                    headers={"Metadata-Flavor": "Google"},
                )
                response.raise_for_status()
                reference = response.text.strip()
                if not VERSION_PATTERN.fullmatch(reference):
                    raise ValueError("Invalid numeric secret version reference")
                references[environment_name] = reference

            response = client.get(
                f"{METADATA_ROOT}/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            response.raise_for_status()
            access_token = response.json()["access_token"]
            if not isinstance(access_token, str) or not access_token.strip():
                raise ValueError("Missing access token")

            credentials: dict[str, str] = {}
            for environment_name, reference in references.items():
                response = client.get(
                    f"{API_ROOT}/{reference}:access",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                payload = response.json()["payload"]
                data = base64.b64decode(payload["data"], validate=True)
                if google_crc32c.value(data) != int(payload["dataCrc32c"]):
                    raise ValueError("Secret checksum mismatch")
                value = data.decode("utf-8")
                if not value or value != value.strip() or any(ord(c) < 32 for c in value):
                    raise ValueError("Secret must be nonempty, single-line UTF-8")
                if environment_name == "CONFIDENTIAL_SHARED_SECRET" and len(data) < 32:
                    raise ValueError("Shared key must contain at least 32 bytes")
                credentials[environment_name] = value
            return credentials
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        # Response bodies and exception chains can contain credential material.
        raise RuntimeError(
            "Secret Manager bootstrap failed; check version references, enabled versions, "
            "service-account permissions, and secret formatting."
        ) from None
