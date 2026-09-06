from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx

from .config import Settings
from .security import sign_bytes


class ReceiptError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiptResult:
    remaining_credits: float | None
    guest_cleans_left: int | None
    credits_used: float


class ReceiptClient:
    def __init__(self, settings: Settings) -> None:
        self._url = settings.receipt_url
        self._secret = settings.shared_secret
        self._timeout = settings.receipt_timeout_seconds

    async def submit(self, receipt: dict[str, object], *, attempts: int = 3) -> ReceiptResult:
        body = json.dumps(
            receipt,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-SimpleUnmark-Signature": sign_bytes(body, self._secret),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(self._url, content=body, headers=headers)
                    if response.is_success:
                        payload = response.json()
                        return ReceiptResult(
                            remaining_credits=payload.get("remainingCredits"),
                            guest_cleans_left=payload.get("guestCleansLeft"),
                            credits_used=float(payload.get("creditsUsed") or 0),
                        )
                    if response.status_code in {400, 401, 403, 409}:
                        break
                except (httpx.HTTPError, ValueError, TypeError):
                    pass
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.25 * (4**attempt))
        raise ReceiptError("the content-free usage receipt was rejected or unavailable")
