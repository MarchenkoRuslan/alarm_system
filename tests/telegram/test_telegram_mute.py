from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
)

from tests.test_api import _FakeTelegramClient


def _webhook_payload(text: str, *, user_id: int = 42) -> dict:
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": 500},
            "from": {"id": user_id},
        },
    }


class TelegramMuteCommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAlertStore()
        self.telegram = _FakeTelegramClient()
        self.mute_store = InMemoryMuteStore()
        self.attempt_store = InMemoryDeliveryAttemptStore()
        app = create_app(
            store=self.store,
            telegram_client=self.telegram,
            mute_store=self.mute_store,
            attempt_store=self.attempt_store,
        )
        self.client = TestClient(app)

    def _send(self, text: str) -> None:
        response = self.client.post(
            "/webhooks/telegram",
            json=_webhook_payload(text),
        )
        self.assertEqual(response.status_code, 200)

    def _last_message(self) -> str:
        return self.telegram.messages[-1][1]

    def test_mute_persists_duration_in_store(self) -> None:
        self._send("/mute 30m")
        self.assertIn("Тишина включена на 30m", self._last_message())
        active_until = self.mute_store.get_mute_until("42")
        self.assertIsNotNone(active_until)
        remaining = (active_until - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(29 * 60 <= remaining <= 30 * 60 + 5)

    def test_mute_without_duration_shows_usage(self) -> None:
        self._send("/mute")
        self.assertIn("Используйте", self._last_message())

    def test_mute_parses_hours_and_days(self) -> None:
        self._send("/mute 2h")
        active_until = self.mute_store.get_mute_until("42")
        assert active_until is not None
        remaining = (active_until - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(2 * 3600 - 5 <= remaining <= 2 * 3600 + 5)

        self._send("/mute 1d")
        active_until = self.mute_store.get_mute_until("42")
        assert active_until is not None
        remaining = (active_until - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(86400 - 5 <= remaining <= 86400 + 5)

    def test_mute_rejects_beyond_max_limit(self) -> None:
        self._send("/mute 40d")
        self.assertIn("Максимальная", self._last_message())
        self.assertIsNone(self.mute_store.get_mute_until("42"))

    def test_mute_rejects_bad_duration(self) -> None:
        self._send("/mute forever")
        self.assertIn("Некорректный интервал", self._last_message())

    def test_unmute_clears_active_mute(self) -> None:
        self.mute_store.set_mute(user_id="42", seconds=120)
        self._send("/unmute")
        self.assertIn("Тишина снята", self._last_message())
        self.assertIsNone(self.mute_store.get_mute_until("42"))

    def test_unmute_without_active_mute(self) -> None:
        self._send("/unmute")
        self.assertIn("не была включена", self._last_message())

    def test_mute_scope_is_per_user(self) -> None:
        self._send("/mute 30m")
        # Another user must not inherit the mute.
        self.assertIsNone(self.mute_store.get_mute_until("99"))
