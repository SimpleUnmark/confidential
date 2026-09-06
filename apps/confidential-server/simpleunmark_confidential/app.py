from __future__ import annotations

import asyncio
import json
import struct
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse, Response, StreamingResponse

from .attestation import (
    AttestationError,
    AttestationProvider,
    ConfidentialSpaceAttestation,
    DevelopmentAttestation,
)
from .config import Settings
from .crypto import (
    HPKE_SUITE_NAME,
    PayloadEncryptionError,
    WorkloadEncryptionKey,
    attestation_binding,
)
from .media import (
    EXTENSIONS_BY_KIND,
    MediaInput,
    MediaResult,
    clean_media,
    decode_media_request,
    encode_media_response,
)
from .provider import (
    DeepInfraProvider,
    ProviderDelta,
    ProviderResult,
    RewriteProvider,
)
from .receipts import ReceiptClient, ReceiptError
from .security import Capability, CapabilityError, verify_capability
from .text import WATERMARKS_REMOVER_VERSION, clean_deterministically, count_words


class CleanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=60_000)


class EncryptedCleanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int
    enc: str = Field(min_length=80, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    ciphertext: str = Field(min_length=24, max_length=400_000, pattern=r"^[A-Za-z0-9_-]+$")


class AttestationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    challenge: str = Field(min_length=32, max_length=74, pattern=r"^[A-Za-z0-9_-]+$")


@dataclass(slots=True)
class AttestationEntry:
    binding: str
    token: str
    key: WorkloadEncryptionKey
    expires_at: int
    decrypt_attempts: int = 0


@dataclass(slots=True)
class PendingAttestation:
    challenge: str
    task: asyncio.Task[AttestationEntry]


MEDIA_ENCRYPTED_MAGIC = b"SUME1"
MAX_MEDIA_ENVELOPE_OVERHEAD = 4_096


def _failure(message: str, status: int, code: str = "SERVICE") -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message, "code": code},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _read_encrypted_input(request: Request, max_bytes: int) -> EncryptedCleanInput:
    body = await _read_body(request, max_bytes)
    try:
        return EncryptedCleanInput.model_validate_json(body)
    except ValidationError as error:
        raise ValueError("INVALID_BODY") from error


async def _read_clean_input(request: Request, max_bytes: int) -> CleanInput:
    body = await _read_body(request, max_bytes)
    try:
        return CleanInput.model_validate_json(body)
    except ValidationError as error:
        raise ValueError("INVALID_BODY") from error


async def _read_attestation_input(request: Request) -> AttestationInput:
    body = await _read_body(request, 1_024)
    try:
        return AttestationInput.model_validate_json(body)
    except ValidationError as error:
        raise ValueError("INVALID_BODY") from error


async def _read_encrypted_media_input(
    request: Request,
    max_bytes: int,
) -> tuple[bytes, bytes]:
    body = bytes(
        await _read_body(
            request,
            max_bytes + MAX_MEDIA_ENVELOPE_OVERHEAD,
            timeout_seconds=120,
        )
    )
    if len(body) < len(MEDIA_ENCRYPTED_MAGIC) + 2 + 1 or not body.startswith(
        MEDIA_ENCRYPTED_MAGIC
    ):
        raise ValueError("INVALID_BODY")
    encapsulated_size = struct.unpack(">H", body[5:7])[0]
    ciphertext_offset = 7 + encapsulated_size
    if encapsulated_size != 65 or ciphertext_offset >= len(body):
        raise ValueError("INVALID_BODY")
    return body[7:ciphertext_offset], body[ciphertext_offset:]


async def _read_body(
    request: Request,
    max_bytes: int,
    timeout_seconds: float = 15,
) -> bytearray:
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdecimal() and int(declared_length) > max_bytes:
        raise ValueError("REQUEST_TOO_LARGE")

    body = bytearray()
    try:
        async with asyncio.timeout(timeout_seconds):
            async for chunk in request.stream():
                # Check before extending so one very large ASGI chunk is not
                # copied into a second, temporarily unbounded allocation.
                if len(body) + len(chunk) > max_bytes:
                    raise ValueError("REQUEST_TOO_LARGE")
                body.extend(chunk)
    except TimeoutError as error:
        raise ValueError("REQUEST_TIMEOUT") from error
    return body


def _receipt_base(capability: Capability, status: str) -> dict[str, object]:
    return {
        "v": capability.v,
        "requestId": capability.requestId,
        "status": status,
        "words": capability.words,
        "characters": capability.characters,
        "inputBytes": capability.inputBytes,
        "emittedAt": int(time.time()),
        "mode": capability.mode,
        "assetKind": capability.assetKind,
        "mediaOperation": capability.mediaOperation,
    }


def _telemetry(
    provider: ProviderResult | None,
    *,
    deterministic_ms: int,
    total_ms: int,
    strong_applied: bool,
) -> dict[str, object]:
    return {
        "source": "watermarks-remover",
        "strongApplied": strong_applied,
        "model": provider.model if provider else None,
        "serviceTier": provider.service_tier if provider else None,
        "providerRequestId": provider.request_id if provider else None,
        "promptTokens": provider.prompt_tokens if provider else 0,
        "completionTokens": provider.completion_tokens if provider else 0,
        "totalTokens": provider.total_tokens if provider else 0,
        "providerCostMicros": provider.cost_micros if provider else 0,
        "finishReason": provider.finish_reason if provider else None,
        "deterministicMs": deterministic_ms,
        "providerMs": provider.provider_ms if provider else 0,
        "totalMs": total_ms,
        "providerAttempts": provider.attempts if provider else 0,
    }


def _plain_event(event: dict[str, object]) -> bytes:
    return (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _validated_text(clean_input: CleanInput, capability: Capability) -> str:
    if capability.assetKind != "TEXT":
        raise ValueError("ASSET_KIND_MISMATCH")
    text = clean_input.text
    if not text.strip():
        raise ValueError("EMPTY_TEXT")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise ValueError("INVALID_UNICODE")
    words = count_words(text)
    if words == 0:
        raise ValueError("NO_WORDS")
    if (
        words != capability.words
        or len(text) != capability.characters
        or len(text.encode()) != capability.inputBytes
    ):
        raise ValueError("MEASUREMENTS_MISMATCH")
    return text


async def _processing_stream(
    capability: Capability,
    text: str,
    *,
    rewrite_provider: RewriteProvider,
    receipt_client: ReceiptClient,
    encode_event: Callable[[dict[str, object]], bytes],
) -> AsyncIterator[bytes]:
    started_at = time.perf_counter()
    deterministic_started_at = time.perf_counter()
    yield encode_event({"type": "phase", "phase": "cleaning"})
    deterministic = clean_deterministically(text)
    deterministic_ms = round((time.perf_counter() - deterministic_started_at) * 1000)
    yield encode_event({"type": "phase", "phase": "rephrasing"})

    provider_result: ProviderResult | None = None
    try:
        async for event in rewrite_provider.stream(deterministic.text):
            if isinstance(event, ProviderDelta):
                yield encode_event({"type": "delta", "text": event.text})
            else:
                provider_result = event

        if provider_result is None or not provider_result.text:
            elapsed = round((time.perf_counter() - started_at) * 1000)
            receipt = {
                **_receipt_base(capability, "failed"),
                "errorCode": "REWRITE_FAILED",
                "telemetry": _telemetry(
                    provider_result,
                    deterministic_ms=deterministic_ms,
                    total_ms=elapsed,
                    strong_applied=False,
                ),
            }
            balances = await receipt_client.submit(receipt)
            if deterministic.removed == 0 and deterministic.normalized_spaces == 0:
                yield encode_event(
                    {
                        "type": "error",
                        "error": {
                            "ok": False,
                            "error": (
                                "Text cleaning could not be completed. "
                                "Your credits were returned."
                            ),
                            "code": "SERVICE",
                            "remainingCredits": balances.remaining_credits,
                            "guestCleansLeft": balances.guest_cleans_left,
                        },
                    }
                )
                return
            yield encode_event(
                {
                    "type": "complete",
                    "result": {
                        "ok": True,
                        "requestId": capability.requestId,
                        "cleanedText": deterministic.text,
                        "words": capability.words,
                        "characters": capability.characters,
                        "creditsUsed": 0,
                        "remainingCredits": balances.remaining_credits,
                        "guestCleansLeft": balances.guest_cleans_left,
                        "removed": deterministic.removed,
                        "normalizedSpaces": deterministic.normalized_spaces,
                        "source": "watermarks-remover",
                        "strongApplied": False,
                        "warning": (
                            "The wording rewrite could not be completed, so only hidden "
                            "characters were cleaned. Your credits were returned."
                        ),
                        "privacyMode": capability.mode,
                    },
                }
            )
            return

        elapsed = round((time.perf_counter() - started_at) * 1000)
        receipt = {
            **_receipt_base(capability, "succeeded"),
            "telemetry": _telemetry(
                provider_result,
                deterministic_ms=deterministic_ms,
                total_ms=elapsed,
                strong_applied=True,
            ),
        }
        balances = await receipt_client.submit(receipt)
        yield encode_event(
            {
                "type": "complete",
                "result": {
                    "ok": True,
                    "requestId": capability.requestId,
                    "cleanedText": provider_result.text,
                    "words": capability.words,
                    "characters": capability.characters,
                    "creditsUsed": balances.credits_used,
                    "remainingCredits": balances.remaining_credits,
                    "guestCleansLeft": balances.guest_cleans_left,
                    "removed": deterministic.removed,
                    "normalizedSpaces": deterministic.normalized_spaces,
                    "source": "watermarks-remover",
                    "strongApplied": True,
                    "warning": None,
                    "privacyMode": capability.mode,
                },
            }
        )
    except asyncio.CancelledError:
        elapsed = round((time.perf_counter() - started_at) * 1000)
        failure = {
            **_receipt_base(capability, "failed"),
            "errorCode": "REQUEST_ABORTED",
            "telemetry": _telemetry(
                provider_result,
                deterministic_ms=deterministic_ms,
                total_ms=elapsed,
                strong_applied=False,
            ),
        }
        task = asyncio.create_task(receipt_client.submit(failure))
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, ReceiptError):
            pass
        raise
    except Exception:
        elapsed = round((time.perf_counter() - started_at) * 1000)
        failure = {
            **_receipt_base(capability, "failed"),
            "errorCode": "PROCESSING_FAILED",
            "telemetry": _telemetry(
                provider_result,
                deterministic_ms=deterministic_ms,
                total_ms=elapsed,
                strong_applied=False,
            ),
        }
        try:
            balances = await receipt_client.submit(failure)
        except ReceiptError:
            balances = None
        yield encode_event(
            {
                "type": "error",
                "error": {
                    "ok": False,
                    "error": (
                        "We could not clean that text. "
                        "Any reserved credits will be returned."
                    ),
                    "code": "SERVICE",
                    "remainingCredits": balances.remaining_credits if balances else None,
                    "guestCleansLeft": balances.guest_cleans_left if balances else None,
                },
            }
        )


def create_app(
    settings: Settings,
    *,
    provider: RewriteProvider | None = None,
    receipts: ReceiptClient | None = None,
    attestation: AttestationProvider | None = None,
) -> FastAPI:
    rewrite_provider = provider or DeepInfraProvider(settings)
    receipt_client = receipts or ReceiptClient(settings)
    attestation_provider = attestation or (
        ConfidentialSpaceAttestation(settings.attestation_audience)
        if settings.require_client_attestation
        else DevelopmentAttestation()
    )
    attestation_cache: dict[str, AttestationEntry] = {}
    pending_attestations: dict[str, PendingAttestation] = {}
    attestation_lock = asyncio.Lock()
    attestation_schedule_lock = asyncio.Lock()
    # The v1 Terraform target has two vCPUs. Bound media work to one job so
    # simultaneous FFmpeg/re-encoding processes cannot exhaust enclave memory.
    media_processing_lock = asyncio.Lock()
    next_attestation_mint_at = 0.0

    async def run_media_job(media: MediaInput) -> MediaResult:
        """Keep the worker accounted for even if its HTTP caller disconnects."""

        task = asyncio.create_task(asyncio.to_thread(clean_media, media))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop its native thread. Wait for it
            # before releasing the single-job lock, then propagate cancellation.
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            raise

    def prune_attestations(now: int) -> None:
        for request_id, entry in list(attestation_cache.items()):
            if entry.expires_at <= now:
                del attestation_cache[request_id]

    def attestation_response(entry: AttestationEntry) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "token": entry.token,
                "encryption": {
                    "suite": HPKE_SUITE_NAME,
                    "publicKey": entry.key.public_key,
                    "keyId": entry.key.key_id,
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    async def mint_attestation_token(binding: str) -> str:
        """Stay below Confidential Space's five custom-token mints per second."""

        nonlocal next_attestation_mint_at
        if settings.require_client_attestation:
            async with attestation_schedule_lock:
                now = asyncio.get_running_loop().time()
                scheduled_at = max(now, next_attestation_mint_at)
                delay = scheduled_at - now
                if delay > 3:
                    raise AttestationError("Confidential Space attestation queue is full")
                # A small margin avoids landing on the service's window edge.
                next_attestation_mint_at = scheduled_at + 0.21
            if delay:
                await asyncio.sleep(delay)
        return await attestation_provider.token(binding)

    async def mint_attestation(
        capability: Capability,
        challenge: str,
        key: WorkloadEncryptionKey,
        binding: str,
    ) -> AttestationEntry:
        """Mint and cache one token for every request ID, even under retries."""

        current_task = asyncio.current_task()
        try:
            token = await mint_attestation_token(binding)
            entry = AttestationEntry(
                binding=binding,
                token=token,
                key=key,
                expires_at=capability.exp,
            )
            async with attestation_lock:
                prune_attestations(int(time.time()))
                pending = pending_attestations.get(capability.requestId)
                if pending and pending.task is current_task:
                    pending_attestations.pop(capability.requestId, None)
                    attestation_cache[capability.requestId] = entry
            return entry
        finally:
            # A failed or cancelled launcher request must release its reserved
            # cache slot so a later browser retry can mint a fresh token.
            async with attestation_lock:
                pending = pending_attestations.get(capability.requestId)
                if pending and pending.task is current_task:
                    pending_attestations.pop(capability.requestId, None)

    app = FastAPI(
        title="Simple Unmark Confidential Workload",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.get("/healthz")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/info", response_model=None)
    async def info() -> dict[str, object]:
        return {
            "protocolVersion": 4,
            "cleanerVersion": WATERMARKS_REMOVER_VERSION,
            # Informational only. Clients must use a verified Confidential
            # Space attestation—not this self-report—as the security proof.
            "declaredRevision": settings.revision,
            "clientAttestationRequired": settings.require_client_attestation,
            "media": {
                "maxBytes": settings.max_media_bytes,
                "extensions": sorted(
                    extension
                    for extensions in EXTENSIONS_BY_KIND.values()
                    for extension in extensions
                ),
                "operations": ["metadata", "audio-purify"],
                "scope": "metadata-and-provenance-with-opt-in-destructive-audio",
            },
        }

    @app.post("/v1/attestation")
    async def attest(request: Request) -> JSONResponse:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return _failure("A valid cleaning authorization is required.", 401, "AUTH")
        try:
            capability = verify_capability(
                authorization.removeprefix("Bearer ").strip(),
                settings.shared_secret,
            )
        except CapabilityError:
            return _failure("The cleaning authorization is invalid or expired.", 401, "AUTH")
        if capability.mode != "CONFIDENTIAL":
            return _failure("This authorization cannot request attestation.", 403, "AUTH")
        origin = request.headers.get("origin", "").rstrip("/")
        if origin != capability.origin or origin not in settings.allowed_origins:
            return _failure("This origin is not authorized for attestation.", 403, "AUTH")
        try:
            body = await _read_attestation_input(request)
        except ValueError as error:
            status = (
                413
                if str(error) == "REQUEST_TOO_LARGE"
                else 408
                if str(error) == "REQUEST_TIMEOUT"
                else 400
            )
            return _failure("The attestation request is invalid.", status, "INVALID")
        async with attestation_lock:
            now = int(time.time())
            prune_attestations(now)
            cached = attestation_cache.get(capability.requestId)
            if cached:
                binding = attestation_binding(
                    challenge=body.challenge,
                    request_id=capability.requestId,
                    public_key=cached.key.public_key,
                )
                if cached.binding != binding:
                    return _failure("This authorization was already attested.", 409, "AUTH")
                return attestation_response(cached)
            pending = pending_attestations.get(capability.requestId)
            if pending:
                if pending.challenge != body.challenge:
                    return _failure("This authorization is already being attested.", 409, "AUTH")
                attestation_task = pending.task
            elif len(attestation_cache) + len(pending_attestations) >= 2_000:
                return _failure("Workload attestation is busy. Try again shortly.", 503, "SERVICE")
            else:
                encryption_key = WorkloadEncryptionKey()
                binding = attestation_binding(
                    challenge=body.challenge,
                    request_id=capability.requestId,
                    public_key=encryption_key.public_key,
                )
                attestation_task = asyncio.create_task(
                    mint_attestation(
                        capability,
                        body.challenge,
                        encryption_key,
                        binding,
                    )
                )
                pending_attestations[capability.requestId] = PendingAttestation(
                    challenge=body.challenge,
                    task=attestation_task,
                )

        # Identical concurrent browser retries share the same launcher token
        # request. Shielding keeps one disconnected caller from cancelling the
        # mint for every other waiter.
        try:
            entry = await asyncio.shield(attestation_task)
        except AttestationError:
            return _failure("Workload attestation is unavailable.", 503, "SERVICE")
        return attestation_response(entry)

    @app.post("/v1/plain/clean", response_model=None)
    async def clean_plain(request: Request) -> JSONResponse | StreamingResponse:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return _failure("A valid cleaning authorization is required.", 401, "AUTH")
        try:
            capability = verify_capability(
                authorization.removeprefix("Bearer ").strip(),
                settings.shared_secret,
            )
        except CapabilityError:
            return _failure("The cleaning authorization is invalid or expired.", 401, "AUTH")
        if capability.mode != "PRIVATE":
            return _failure("This authorization cannot use the private endpoint.", 403, "AUTH")
        if capability.assetKind != "TEXT":
            return _failure("This authorization cannot clean text.", 403, "AUTH")

        origin = request.headers.get("origin", "").rstrip("/")
        if origin != capability.origin or origin not in settings.allowed_origins:
            return _failure("This origin is not authorized for the cleaning service.", 403, "AUTH")

        try:
            clean_input = await _read_clean_input(request, settings.max_body_bytes)
            text = _validated_text(clean_input, capability)
        except ValueError as error:
            if str(error) == "REQUEST_TOO_LARGE":
                return _failure("The request is too large.", 413, "LIMIT")
            if str(error) == "REQUEST_TIMEOUT":
                return _failure("The request body timed out.", 408, "INVALID")
            if str(error) == "MEASUREMENTS_MISMATCH":
                return _failure("The authorized text measurements do not match.", 403, "AUTH")
            if str(error) == "EMPTY_TEXT":
                return _failure("Paste some text first.", 400, "INVALID")
            if str(error) == "NO_WORDS":
                return _failure("Paste text containing at least one word.", 400, "INVALID")
            return _failure("The text is invalid.", 400, "INVALID")

        try:
            await receipt_client.submit(_receipt_base(capability, "started"), attempts=1)
        except ReceiptError:
            return _failure("The cleaning reservation could not be claimed.", 409, "SERVICE")

        return StreamingResponse(
            _processing_stream(
                capability,
                text,
                rewrite_provider=rewrite_provider,
                receipt_client=receipt_client,
                encode_event=_plain_event,
            ),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/clean", response_model=None)
    async def clean(request: Request) -> JSONResponse | StreamingResponse:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return _failure("A valid cleaning authorization is required.", 401, "AUTH")
        try:
            capability = verify_capability(
                authorization.removeprefix("Bearer ").strip(),
                settings.shared_secret,
            )
        except CapabilityError:
            return _failure("The cleaning authorization is invalid or expired.", 401, "AUTH")
        if capability.mode != "CONFIDENTIAL":
            return _failure("This authorization cannot use the encrypted endpoint.", 403, "AUTH")
        if capability.assetKind != "TEXT":
            return _failure("This authorization cannot clean text.", 403, "AUTH")

        origin = request.headers.get("origin", "").rstrip("/")
        if origin != capability.origin or origin not in settings.allowed_origins:
            return _failure("This origin is not authorized for the cleaning service.", 403, "AUTH")

        try:
            encrypted_input = await _read_encrypted_input(request, settings.max_body_bytes)
        except ValueError as error:
            if str(error) == "REQUEST_TOO_LARGE":
                return _failure("The request is too large.", 413, "LIMIT")
            if str(error) == "REQUEST_TIMEOUT":
                return _failure("The request body timed out.", 408, "INVALID")
            return _failure("The encrypted request body is invalid.", 400, "INVALID")
        if encrypted_input.v != 1:
            return _failure("The encrypted request version is unsupported.", 400, "INVALID")

        async with attestation_lock:
            prune_attestations(int(time.time()))
            attestation_entry = attestation_cache.get(capability.requestId)
            if not attestation_entry:
                return _failure(
                    "Verify this workload's attestation before cleaning.",
                    428,
                    "AUTH",
                )
            attestation_entry.decrypt_attempts += 1
            if attestation_entry.decrypt_attempts > 5:
                attestation_cache.pop(capability.requestId, None)
                return _failure(
                    "Verify this workload's attestation again before cleaning.",
                    428,
                    "AUTH",
                )
        try:
            plaintext, response_cipher = attestation_entry.key.decrypt_request(
                request_id=capability.requestId,
                encapsulated_key=encrypted_input.enc,
                ciphertext=encrypted_input.ciphertext,
            )
            clean_input = CleanInput.model_validate_json(plaintext)
        except (PayloadEncryptionError, ValidationError):
            return _failure("The encrypted request could not be opened.", 400, "INVALID")

        try:
            text = _validated_text(clean_input, capability)
        except ValueError as error:
            if str(error) == "MEASUREMENTS_MISMATCH":
                return _failure("The authorized text measurements do not match.", 403, "AUTH")
            if str(error) == "EMPTY_TEXT":
                return _failure("Paste some text first.", 400, "INVALID")
            if str(error) == "NO_WORDS":
                return _failure("Paste text containing at least one word.", 400, "INVALID")
            return _failure("The text contains invalid Unicode data.", 400, "INVALID")

        # Atomically claim the database reservation before spending provider
        # tokens. This callback contains measurements and a request ID only.
        try:
            await receipt_client.submit(_receipt_base(capability, "started"), attempts=1)
        except ReceiptError:
            return _failure("The cleaning reservation could not be claimed.", 409, "SERVICE")
        async with attestation_lock:
            attestation_cache.pop(capability.requestId, None)

        return StreamingResponse(
            _processing_stream(
                capability,
                text,
                rewrite_provider=rewrite_provider,
                receipt_client=receipt_client,
                encode_event=response_cipher.encrypt_event,
            ),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/media/clean", response_model=None)
    async def clean_encrypted_media(request: Request) -> JSONResponse | Response:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return _failure("A valid cleaning authorization is required.", 401, "AUTH")
        try:
            capability = verify_capability(
                authorization.removeprefix("Bearer ").strip(),
                settings.shared_secret,
            )
        except CapabilityError:
            return _failure("The cleaning authorization is invalid or expired.", 401, "AUTH")
        if capability.mode != "CONFIDENTIAL" or capability.assetKind == "TEXT":
            return _failure("This authorization cannot clean media.", 403, "AUTH")

        origin = request.headers.get("origin", "").rstrip("/")
        if origin != capability.origin or origin not in settings.allowed_origins:
            return _failure("This origin is not authorized for the cleaning service.", 403, "AUTH")

        try:
            encapsulated_key, ciphertext = await _read_encrypted_media_input(
                request,
                settings.max_media_bytes,
            )
        except ValueError as error:
            if str(error) == "REQUEST_TOO_LARGE":
                return _failure("The media file is too large.", 413, "LIMIT")
            if str(error) == "REQUEST_TIMEOUT":
                return _failure("The media upload timed out.", 408, "INVALID")
            return _failure("The encrypted media request is invalid.", 400, "INVALID")

        async with attestation_lock:
            prune_attestations(int(time.time()))
            attestation_entry = attestation_cache.get(capability.requestId)
            if not attestation_entry:
                return _failure(
                    "Verify this workload's attestation before cleaning.",
                    428,
                    "AUTH",
                )
            attestation_entry.decrypt_attempts += 1
            if attestation_entry.decrypt_attempts > 5:
                attestation_cache.pop(capability.requestId, None)
                return _failure(
                    "Verify this workload's attestation again before cleaning.",
                    428,
                    "AUTH",
                )

        try:
            plaintext, response_cipher = attestation_entry.key.decrypt_request_bytes(
                request_id=capability.requestId,
                encapsulated_key=encapsulated_key,
                ciphertext=ciphertext,
            )
            media = decode_media_request(plaintext)
        except (PayloadEncryptionError, ValueError):
            return _failure("The encrypted media request could not be opened.", 400, "INVALID")

        if (
            media.asset_kind != capability.assetKind
            or media.extension != capability.fileExtension
            or media.mime_type != capability.mimeType
            or media.operation != capability.mediaOperation
            or len(media.data) != capability.inputBytes
        ):
            return _failure("The authorized media measurements do not match.", 403, "AUTH")

        # Reject instead of queueing encrypted media in RAM. The reservation is
        # still pending at this point, so the browser can abandon it and retry.
        if media_processing_lock.locked():
            return _failure(
                "Media cleaning is busy. Try this file again shortly.",
                429,
                "BUSY",
            )
        await media_processing_lock.acquire()
        try:
            try:
                await receipt_client.submit(_receipt_base(capability, "started"), attempts=1)
            except ReceiptError:
                return _failure(
                    "The cleaning reservation could not be claimed.", 409, "SERVICE"
                )
            async with attestation_lock:
                attestation_cache.pop(capability.requestId, None)

            started_at = time.perf_counter()
            try:
                result = await run_media_job(media)
                elapsed = round((time.perf_counter() - started_at) * 1_000)
                balances = await receipt_client.submit(
                    {
                        **_receipt_base(capability, "succeeded"),
                        "telemetry": _telemetry(
                            None,
                            deterministic_ms=elapsed,
                            total_ms=elapsed,
                            strong_applied=result.changed,
                        ),
                    }
                )
            except Exception:
                elapsed = round((time.perf_counter() - started_at) * 1_000)
                try:
                    await receipt_client.submit(
                        {
                            **_receipt_base(capability, "failed"),
                            "errorCode": "MEDIA_PROCESSING_FAILED",
                            "telemetry": _telemetry(
                                None,
                                deterministic_ms=elapsed,
                                total_ms=elapsed,
                                strong_applied=False,
                            ),
                        }
                    )
                except ReceiptError:
                    pass
                return _failure(
                    "We could not safely clean that file. Reserved credits were returned.",
                    422,
                    "SERVICE",
                )

            response_header: dict[str, object] = {
                "v": 1,
                "requestId": capability.requestId,
                "assetKind": result.asset_kind,
                "operation": result.operation,
                "extension": result.extension,
                "mimeType": result.mime_type,
                "inputBytes": capability.inputBytes,
                "outputBytes": len(result.data),
                "changed": result.changed,
                "actions": list(result.actions),
                "warning": result.residual_warning,
                "creditsUsed": balances.credits_used,
                "remainingCredits": balances.remaining_credits,
                "guestCleansLeft": balances.guest_cleans_left,
            }
            encrypted_response = response_cipher.encrypt_payload(
                encode_media_response(response_header, result.data)
            )
            return Response(
                encrypted_response,
                media_type="application/octet-stream",
                headers={
                    "Cache-Control": "no-store, no-transform",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        finally:
            media_processing_lock.release()

    return app
