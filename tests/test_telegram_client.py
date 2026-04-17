from __future__ import annotations

import io
import json
import unittest
from json import JSONDecodeError
from unittest.mock import patch
from urllib.error import HTTPError

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

    def test_send_message_blocking_raises_for_http_error_with_description(self) -> None:
        client = TelegramApiClient(bot_token="token")
        error_payload = json.dumps(
            {"ok": False, "description": "Bad Request: chat not found"}
        ).encode("utf-8")
        http_error = HTTPError(
            url="https://api.telegram.org/bottoken/sendMessage",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(error_payload),
        )

        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client._send_message_blocking(chat_id="missing", text="hello")
        self.assertIn("chat not found", str(ctx.exception))

    def test_set_webhook_blocking_sends_secret_token_when_provided(self) -> None:
        client = TelegramApiClient(bot_token="token")
        payload = json.dumps({"ok": True, "result": True}).encode("utf-8")
        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            return_value=_FakeResponse(payload),
        ) as urlopen_mock:
            client._set_webhook_blocking(
                webhook_url="https://example.com/webhooks/telegram",
                secret_token="secret-1",
            )

        request_obj = urlopen_mock.call_args[0][0]
        self.assertIn("/setWebhook", request_obj.full_url)
        sent_payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(
            sent_payload["url"],
            "https://example.com/webhooks/telegram",
        )
        self.assertEqual(sent_payload["secret_token"], "secret-1")

    def test_get_webhook_info_blocking_calls_correct_api_method(self) -> None:
        client = TelegramApiClient(bot_token="token")
        payload = json.dumps(
            {"ok": True, "result": {"url": "https://example.com/webhooks/telegram"}}
        ).encode("utf-8")
        with patch(
            "alarm_system.api.telegram_client.request.urlopen",
            return_value=_FakeResponse(payload),
        ) as urlopen_mock:
            response = client._get_webhook_info_blocking()

        request_obj = urlopen_mock.call_args[0][0]
        self.assertIn("/getWebhookInfo", request_obj.full_url)
        self.assertEqual(
            response["result"]["url"],
            "https://example.com/webhooks/telegram",
        )
