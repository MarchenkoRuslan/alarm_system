from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
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

    def _send_message_blocking(self, *, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = request.Request(
            url=url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("description") or "telegram error"))
