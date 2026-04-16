from __future__ import annotations

import json
import unittest
from json import JSONDecodeError
from unittest.mock import patch

from alarm_system.api.telegram_client import TelegramApiClient


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


class TelegramClientTests(unittest.TestCase):
    def test_send_message_blocking_success(self) -> None:
        client = TelegramApiClient(bot_token="token")
        payload = json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")

        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            client._send_message_blocking(chat_id="123", text="hello")

    def test_send_message_blocking_raises_for_ok_false(self) -> None:
        client = TelegramApiClient(bot_token="token")
        payload = json.dumps(
            {"ok": False, "description": "Bad Request: chat not found"}
        ).encode("utf-8")

        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            return_value=_FakeResponse(payload),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client._send_message_blocking(chat_id="missing", text="hello")
        self.assertIn("chat not found", str(ctx.exception))

    def test_send_message_blocking_raises_for_invalid_json(self) -> None:
        client = TelegramApiClient(bot_token="token")
        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            return_value=_FakeResponse(b"{invalid_json"),
        ):
            with self.assertRaises(JSONDecodeError):
                client._send_message_blocking(chat_id="123", text="hello")
