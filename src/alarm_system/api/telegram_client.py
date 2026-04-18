from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class TelegramApiClient:
    bot_token: str
    timeout_seconds: int = 5

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._send_message_blocking,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._edit_message_text_blocking,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._answer_callback_query_blocking,
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
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

    async def set_my_commands(
        self,
        *,
        commands: list[dict[str, str]],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._set_my_commands_blocking,
            commands=commands,
        )

    def _send_message_blocking(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        payload = self._call_api_blocking(method="sendMessage", body=body)
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("description") or "telegram error"))
        return payload

    def _edit_message_text_blocking(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if parse_mode is not None:
            body["parse_mode"] = parse_mode
        return self._call_api_blocking(method="editMessageText", body=body)

    def _answer_callback_query_blocking(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            body["text"] = text
        return self._call_api_blocking(
            method="answerCallbackQuery",
            body=body,
        )

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

    def _set_my_commands_blocking(
        self,
        *,
        commands: list[dict[str, str]],
    ) -> dict[str, Any]:
        return self._call_api_blocking(
            method="setMyCommands",
            body={"commands": commands},
        )

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
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            raw_error = ""
            if exc.fp is not None:
                try:
                    raw_error = exc.fp.read().decode("utf-8")
                except Exception:  # noqa: BLE001
                    raw_error = ""
            description = self._extract_error_description(raw_error)
            if description is None:
                description = f"Telegram API HTTP {exc.code}: {exc.reason}"
            raise RuntimeError(description) from exc
        payload = json.loads(raw)
        if payload.get("ok") is not True:
            raise RuntimeError(str(payload.get("description") or "telegram error"))
        return payload

    @staticmethod
    def _extract_error_description(raw_error: str) -> str | None:
        if not raw_error.strip():
            return None
        try:
            payload = json.loads(raw_error)
        except json.JSONDecodeError:
            return raw_error.strip()
        description = payload.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
        return None
