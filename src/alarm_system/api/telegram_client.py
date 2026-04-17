from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import request


@dataclass(frozen=True)
class TelegramApiClient:
    bot_token: str
    timeout_seconds: int = 5

    async def send_message(self, *, chat_id: str, text: str) -> None:
        await asyncio.to_thread(
            self._send_message_blocking,
            chat_id=chat_id,
            text=text,
        )

    async def set_webhook(
        self,
        *,
        webhook_url: str,
        secret_token: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._set_webhook_blocking,
            webhook_url=webhook_url,
            secret_token=secret_token,
        )

    async def get_webhook_info(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_webhook_info_blocking)

    def _send_message_blocking(self, *, chat_id: str, text: str) -> None:
        payload = self._call_api_blocking(
            method="sendMessage",
            body={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("description") or "telegram error"))

    def _set_webhook_blocking(
        self,
        *,
        webhook_url: str,
        secret_token: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"url": webhook_url}
        if secret_token is not None and secret_token.strip():
            body["secret_token"] = secret_token.strip()
        return self._call_api_blocking(method="setWebhook", body=body)

    def _get_webhook_info_blocking(self) -> dict[str, Any]:
        return self._call_api_blocking(method="getWebhookInfo", body={})

    def _call_api_blocking(
        self,
        *,
        method: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        encoded_body = json.dumps(body).encode("utf-8")
        req = request.Request(
            url=url,
            method="POST",
            data=encoded_body,
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("description") or "telegram error"))
        return payload
