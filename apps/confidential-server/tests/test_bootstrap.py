from __future__ import annotations

import base64
import traceback
from collections.abc import Callable
from typing import Any

import google_crc32c
import httpx
import pytest

from simpleunmark_confidential import bootstrap
from simpleunmark_confidential.config import Settings

DEEPINFRA = "test-provider-key"
SHARED = "s" * 64
TOKEN = "test-service-account-token"
REFERENCES = {
    "simpleunmark-deepinfra-secret-version": (
        "projects/simple-unmark-prod/secrets/simpleunmark-deepinfra-api-key/versions/1"
    ),
    "simpleunmark-shared-secret-version": (
        "projects/simple-unmark-prod/secrets/simpleunmark-confidential-shared-secret/versions/2"
    ),
}


def _payload(value: str) -> dict[str, object]:
    data = value.encode()
    return {
        "payload": {
            "data": base64.b64encode(data).decode(),
            "dataCrc32c": str(google_crc32c.value(data)),
        }
    }


def _reply(request: httpx.Request) -> httpx.Response:
    if request.url.host == "metadata.google.internal":
        assert request.headers["Metadata-Flavor"] == "Google"
        assert "Authorization" not in request.headers
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": TOKEN})
        return httpx.Response(200, text=REFERENCES[request.url.path.split("/")[-1]])
    assert request.url.host == "secretmanager.googleapis.com"
    assert request.url.scheme == "https"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Metadata-Flavor" not in request.headers
    value = DEEPINFRA if "deepinfra" in request.url.path else SHARED
    return httpx.Response(200, json=_payload(value))


def _mock(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response] = _reply,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(bootstrap.httpx, "Client", client)
    monkeypatch.setenv("REQUIRE_CLIENT_ATTESTATION", "1")
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.delenv("CONFIDENTIAL_SHARED_SECRET", raising=False)
    return requests


def test_production_fetches_both_secrets_without_environment_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _mock(monkeypatch)
    credentials = bootstrap.bootstrap_credentials()
    assert credentials == {"DEEPINFRA_API_KEY": DEEPINFRA, "CONFIDENTIAL_SHARED_SECRET": SHARED}
    assert "DEEPINFRA_API_KEY" not in bootstrap.os.environ
    assert "CONFIDENTIAL_SHARED_SECRET" not in bootstrap.os.environ
    assert len(requests) == 5
    monkeypatch.setenv("APP_RECEIPT_URL", "https://simpleunmark.com/api/clean/receipt")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://simpleunmark.com")
    settings = Settings.from_env(credentials=credentials)
    assert settings.shared_secret == SHARED
    assert settings.deepinfra_api_key == DEEPINFRA
    assert SHARED not in repr(settings)
    assert DEEPINFRA not in repr(settings)


def test_production_never_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch)
    monkeypatch.setenv("DEEPINFRA_API_KEY", "untrusted-env-value")
    monkeypatch.setenv("CONFIDENTIAL_SHARED_SECRET", "untrusted" * 10)
    assert bootstrap.bootstrap_credentials()["DEEPINFRA_API_KEY"] == DEEPINFRA
    with pytest.raises(RuntimeError, match="must be loaded from Secret Manager"):
        Settings.from_env()


def test_local_development_makes_no_cloud_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _mock(monkeypatch)
    monkeypatch.delenv("REQUIRE_CLIENT_ATTESTATION")
    monkeypatch.setenv("DEEPINFRA_API_KEY", DEEPINFRA)
    monkeypatch.setenv("CONFIDENTIAL_SHARED_SECRET", SHARED)
    monkeypatch.setenv("APP_RECEIPT_URL", "http://localhost:3000/api/clean/receipt")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    assert bootstrap.bootstrap_credentials() == {}
    assert requests == []
    assert Settings.from_env().shared_secret == SHARED


@pytest.mark.parametrize(
    "reference",
    [
        "https://attacker.example/secret",
        "projects/demo-project/secrets/a/versions/latest",
        "projects/demo-project/secrets/a/versions/0",
        "projects/demo-project/secrets/a/versions/1?redirect=evil",
        "projects/demo-project/secrets/../versions/1",
        "",
    ],
)
def test_rejects_unpinned_or_malformed_references_before_obtaining_token(
    monkeypatch: pytest.MonkeyPatch,
    reference: str,
) -> None:
    requests = _mock(monkeypatch, lambda request: httpx.Response(200, text=reference))
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        bootstrap.bootstrap_credentials()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "403",
        "404",
        "redirect",
        "timeout",
        "checksum",
        "bad-base64",
        "empty",
        "short-shared",
        "newline",
        "missing-payload",
        "missing-token",
    ],
)
def test_fails_closed_without_leaking_response_material(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "missing-token" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={})
        if request.url.host != "secretmanager.googleapis.com":
            return _reply(request)
        if failure in {"403", "404"}:
            return httpx.Response(int(failure), text=f"{TOKEN} {SHARED}")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.example"})
        if failure == "timeout":
            raise httpx.ReadTimeout(f"{TOKEN} {SHARED}")
        if failure == "checksum":
            return httpx.Response(
                200,
                json={
                    "payload": {
                        "data": base64.b64encode(SHARED.encode()).decode(),
                        "dataCrc32c": "0",
                    }
                },
            )
        if failure == "bad-base64":
            return httpx.Response(200, json={"payload": {"data": "!", "dataCrc32c": "1"}})
        if failure == "empty":
            return httpx.Response(200, json=_payload(""))
        if failure == "newline":
            return httpx.Response(200, json=_payload(f"{SHARED}\n"))
        if failure == "short-shared":
            return httpx.Response(200, json=_payload("short"))
        if failure == "missing-payload":
            return httpx.Response(200, json={})
        return _reply(request)

    requests = _mock(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="bootstrap failed") as caught:
        bootstrap.bootstrap_credentials()
    formatted = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in formatted
    assert SHARED not in formatted
    assert "DEEPINFRA_API_KEY" not in bootstrap.os.environ
    assert all(
        r.url.host in {"metadata.google.internal", "secretmanager.googleapis.com"} for r in requests
    )
