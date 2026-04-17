from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
)

from tests.test_api import _FakeTelegramClient


def _update(text: str, *, user_id: int | None = 42, chat_id: int = 500) -> dict:
    message: dict = {
        "text": text,
        "chat": {"id": chat_id},
    }
    if user_id is not None:
        message["from"] = {"id": user_id}
    return {"update_id": 1, "message": message}


class TelegramWebhookNoiseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAlertStore()
        self.telegram = _FakeTelegramClient()
        app = create_app(
            store=self.store,
            telegram_client=self.telegram,
            mute_store=InMemoryMuteStore(),
            attempt_store=InMemoryDeliveryAttemptStore(),
        )
        self.client = TestClient(app)

    def _post(self, update: dict) -> None:
        response = self.client.post("/webhooks/telegram", json=update)
        self.assertEqual(response.status_code, 200)

    def test_non_slash_message_is_silently_ignored(self) -> None:
        self._post(_update("привет"))
        self.assertEqual(self.telegram.messages, [])

    def test_empty_text_is_silently_ignored(self) -> None:
        self._post(_update(""))
        self.assertEqual(self.telegram.messages, [])

    def test_whitespace_only_text_is_silently_ignored(self) -> None:
        self._post(_update("   \t\n  "))
        self.assertEqual(self.telegram.messages, [])

    def test_unknown_slash_command_still_gets_help_hint(self) -> None:
        self._post(_update("/wat"))
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertIn("Неизвестная команда", self.telegram.messages[0][1])

    def test_message_without_from_is_silently_ignored(self) -> None:
        self._post(_update("/help", user_id=None))
        self.assertEqual(self.telegram.messages, [])

    def test_group_chat_gets_single_hint_and_no_state_change(self) -> None:
        self._post(_update("/start", chat_id=-1001234))
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertIn("приватных чатах", self.telegram.messages[0][1])
        self.assertEqual(self.store.list_bindings(user_id="42"), [])

    def test_oversized_text_is_rejected_silently(self) -> None:
        huge_text = "/start " + ("a" * 5000)
        self._post(_update(huge_text))
        self.assertEqual(self.telegram.messages, [])

    def test_long_reply_is_truncated_below_telegram_limit(self) -> None:
        # /create_raw with huge JSON produces a very long validation
        # error. The dispatcher must truncate the outgoing text so the
        # Bot API sendMessage call stays within 4096 chars.
        payload = "{" + ",".join(f'"k{i}":"{i}"' for i in range(1000)) + "}"
        # Reconstruct as a command under the 4096 input limit.
        text = "/create_raw " + payload[:3500]
        self._post(_update(text))
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertLessEqual(len(self.telegram.messages[0][1]), 3900)
