from __future__ import annotations

import os

import httpx

METADATA_ATTRIBUTES_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes"
)
PLAINTEXT_METADATA_CREDENTIALS = {
    "simpleunmark-deepinfra-api-key": "DEEPINFRA_API_KEY",
    "simpleunmark-shared-secret": "CONFIDENTIAL_SHARED_SECRET",
}


def bootstrap_plaintext_metadata_credentials() -> None:
    """Load v1 credentials from ordinary GCE metadata when env vars are absent.

    This intentionally has no attested Secret Manager or KMS dependency. The
    values are protected from network observers by application-layer payload
    encryption, but remain readable by GCP project/VM administrators.
    """
    missing = {
        metadata_name: environment_name
        for metadata_name, environment_name in PLAINTEXT_METADATA_CREDENTIALS.items()
        if not os.environ.get(environment_name, "").strip()
    }
    if not missing:
        return

    try:
        with httpx.Client(
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
            trust_env=False,
        ) as client:
            for metadata_name, environment_name in missing.items():
                response = client.get(f"{METADATA_ATTRIBUTES_URL}/{metadata_name}")
                response.raise_for_status()
                value = response.text.strip()
                if not value:
                    raise RuntimeError(f"metadata attribute {metadata_name} is empty")
                os.environ[environment_name] = value
    except httpx.HTTPError as error:
        names = ", ".join(missing.values())
        raise RuntimeError(
            f"{names} must be set in the environment or ordinary GCE metadata"
        ) from error
