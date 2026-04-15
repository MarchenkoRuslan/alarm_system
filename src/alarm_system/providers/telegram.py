from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib import error, request

from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
)
from alarm_system.entities import DeliveryChannel, DeliveryStatus


@dataclass(frozen=True)
class TelegramProvider(DeliveryProvider):
    """
    Minimal Telegram Bot API provider for MVP delivery.

    Idempotency strategy lives in dispatcher via trigger/channel/
    destination key, so provider stays focused on transport.
    """

    bot_token: str
    parse_mode: str = "Markdown"
    timeout_seconds: int = 5

    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        return await asyncio.to_thread(self._send_blocking, payload)

    def _send_blocking(self, payload: DeliveryPayload) -> DeliveryResult:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": payload.destination,
                "text": payload.body,
                "parse_mode": self.parse_mode,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = request.Request(
            url=url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            return DeliveryResult(
                status=DeliveryStatus.FAILED,
                error_code=f"http_{exc.code}",
                error_detail=str(exc),
                retryable=exc.code >= 500 or exc.code == 429,
            )
        except error.URLError as exc:
            return DeliveryResult(
                status=DeliveryStatus.RETRYING,
                error_code="network_error",
                error_detail=str(exc.reason),
                retryable=True,
            )

        try:
            payload_json = json.loads(raw)
        except json.JSONDecodeError:
            return DeliveryResult(
                status=DeliveryStatus.FAILED,
                error_code="invalid_json",
                error_detail=raw,
                retryable=False,
            )
        ok = payload_json.get("ok") is True
        if not ok:
            error_code = payload_json.get("error_code")
            retryable = False
            if isinstance(error_code, int):
                retryable = error_code == 429 or error_code >= 500
            return DeliveryResult(
                status=DeliveryStatus.FAILED,
                error_code=str(error_code or "telegram_error"),
                error_detail=str(
                    payload_json.get("description") or raw
                ),
                retryable=retryable,
            )
        result = payload_json.get("result") or {}
        message_id = result.get("message_id")
        return DeliveryResult(
            status=DeliveryStatus.SENT,
            provider_message_id=str(message_id) if message_id is not None else None,
            retryable=False,
        )
