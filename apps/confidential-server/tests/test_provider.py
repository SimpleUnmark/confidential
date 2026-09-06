from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from simpleunmark_confidential.config import Settings
from simpleunmark_confidential.provider import (
    DeepInfraProvider,
    ProviderDelta,
    ProviderResult,
)


def _settings(**changes: object) -> Settings:
    settings = Settings(
        shared_secret="x" * 32,
        receipt_url="https://app.example/receipt",
        allowed_origins=("https://simpleunmark.com",),
        deepinfra_api_key="provider-secret",
    )
    return replace(settings, **changes)


def _stream(*chunks: dict[str, object]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}" for chunk in chunks]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


async def _events(provider: DeepInfraProvider) -> list[ProviderDelta | ProviderResult]:
    return [event async for event in provider.stream("Source text")]


@pytest.mark.asyncio
async def test_requires_an_explicit_stop_before_charging() -> None:
    for finish_reason in (None, "length", "content_filter"):
        transport = httpx.MockTransport(
            lambda _request, reason=finish_reason: httpx.Response(
                200,
                content=_stream(
                    {
                        "id": "request-1",
                        "choices": [
                            {
                                "delta": {"content": "Partial output"},
                                "finish_reason": reason,
                            }
                        ],
                    }
                ),
            )
        )
        events = await _events(DeepInfraProvider(_settings(), transport=transport))
        assert isinstance(events[-1], ProviderResult)
        assert events[-1].text is None
        assert events[-1].finish_reason == finish_reason


@pytest.mark.asyncio
async def test_does_not_reuse_a_previous_attempt_finish_reason() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                content=_stream({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            )
        return httpx.Response(
            200,
            content=_stream({"choices": [{"delta": {"content": "Incomplete"}}]}),
        )

    events = await _events(
        DeepInfraProvider(_settings(), transport=httpx.MockTransport(handler))
    )
    assert attempts == 2
    assert isinstance(events[-1], ProviderResult)
    assert events[-1].text is None
    assert events[-1].finish_reason is None


@pytest.mark.asyncio
async def test_accepts_a_normal_stop_and_preserves_usage() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=_stream(
                {"id": "request-1", "choices": [{"delta": {"content": "Complete"}}]},
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "estimated_cost": 0.000005,
                    },
                },
            ),
        )
    )
    events = await _events(DeepInfraProvider(_settings(), transport=transport))
    assert isinstance(events[-1], ProviderResult)
    assert events[-1].text == "Complete"
    assert events[-1].total_tokens == 12
    assert events[-1].cost_micros == 5


@pytest.mark.asyncio
async def test_total_deadline_bounds_a_stalled_transport() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = DeepInfraProvider(
        _settings(provider_timeout_seconds=0.02),
        transport=httpx.MockTransport(handler),
    )
    events = await asyncio.wait_for(_events(provider), timeout=0.25)
    assert isinstance(events[-1], ProviderResult)
    assert events[-1].text is None
    assert events[-1].attempts == 1
