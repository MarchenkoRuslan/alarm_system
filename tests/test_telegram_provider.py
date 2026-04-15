from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from alarm_system.delivery import DeliveryPayload
from alarm_system.entities import DeliveryChannel
from alarm_system.providers.telegram import TelegramProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _payload() -> DeliveryPayload:
    return DeliveryPayload(
        trigger_id="trigger-1",
        alert_id="alert-1",
        user_id="user-1",
        channel=DeliveryChannel.TELEGRAM,
        destination="12345",
        subject="test",
        body="message",
        reason_summary="summary",
    )


class TelegramProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ok_false_429_is_retryable(self) -> None:
        provider = TelegramProvider(bot_token="token")
        with patch(
            "alarm_system.providers.telegram.request.urlopen",
            return_value=_FakeResponse(
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                }
            ),
        ):
            result = await provider.send(_payload())

        self.assertTrue(result.retryable)
        self.assertEqual(result.error_code, "429")

    async def test_ok_false_500_is_retryable(self) -> None:
        provider = TelegramProvider(bot_token="token")
        with patch(
            "alarm_system.providers.telegram.request.urlopen",
            return_value=_FakeResponse(
                {
                    "ok": False,
                    "error_code": 500,
                    "description": "Internal",
                }
            ),
        ):
            result = await provider.send(_payload())

        self.assertTrue(result.retryable)
        self.assertEqual(result.error_code, "500")

    async def test_ok_false_400_is_not_retryable(self) -> None:
        provider = TelegramProvider(bot_token="token")
        with patch(
            "alarm_system.providers.telegram.request.urlopen",
            return_value=_FakeResponse(
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request",
                }
            ),
        ):
            result = await provider.send(_payload())

        self.assertFalse(result.retryable)
        self.assertEqual(result.error_code, "400")
