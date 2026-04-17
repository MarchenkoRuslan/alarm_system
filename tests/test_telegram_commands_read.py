from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
)

from tests.test_api import _FakeTelegramClient


def _make_alert(
    *,
    alert_id: str = "a-1",
    user_id: str = "42",
    enabled: bool = True,
    cooldown: int = 60,
    version: int = 1,
) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": alert_id,
            "rule_id": "r-1",
            "rule_version": 1,
            "user_id": user_id,
            "alert_type": AlertType.VOLUME_SPIKE_5M,
            "filters_json": {},
            "cooldown_seconds": cooldown,
            "channels": [DeliveryChannel.TELEGRAM],
            "enabled": enabled,
            "version": version,
        }
    )


def _make_binding(user_id: str = "42") -> ChannelBinding:
    return ChannelBinding.model_validate(
        {
            "binding_id": f"tg-{user_id}-500",
            "user_id": user_id,
            "channel": DeliveryChannel.TELEGRAM,
            "destination": "500",
            "is_verified": True,
        }
    )


def _webhook_payload(text: str, *, user_id: int = 42) -> dict:
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": 500},
            "from": {"id": user_id},
        },
    }


class TelegramReadCommandsTests(unittest.TestCase):
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

    def _last_message(self) -> str:
        self.assertTrue(self.telegram.messages, "bot did not reply")
        return self.telegram.messages[-1][1]

    def _send(self, text: str) -> None:
        response = self.client.post(
            "/webhooks/telegram",
            json=_webhook_payload(text),
        )
        self.assertEqual(response.status_code, 200)

    def test_alerts_hides_disabled_by_default_and_shows_all_with_flag(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)
        disabled = _make_alert(alert_id="a-2", enabled=False)
        self.store.upsert_alert(disabled, expected_version=0)

        self._send("/alerts")
        reply = self._last_message()
        self.assertIn("a-1", reply)
        self.assertNotIn("a-2", reply)

        self._send("/alerts --all")
        reply = self._last_message()
        self.assertIn("a-1", reply)
        self.assertIn("a-2", reply)

    def test_alerts_empty_state_mentions_templates(self) -> None:
        self._send("/alerts")
        reply = self._last_message()
        self.assertIn("/templates", reply)

    def test_alert_command_shows_details_only_to_owner(self) -> None:
        owned = _make_alert(alert_id="a-owned", user_id="42", cooldown=30)
        foreign = _make_alert(alert_id="a-foreign", user_id="99")
        self.store.upsert_alert(owned, expected_version=0)
        self.store.upsert_alert(foreign, expected_version=0)

        self._send("/alert a-owned")
        reply = self._last_message()
        self.assertIn("a-owned", reply)
        self.assertIn("cooldown: 30s", reply)

        self._send("/alert a-foreign")
        reply = self._last_message()
        self.assertIn("не найден", reply)

    def test_alert_without_id_shows_usage(self) -> None:
        self._send("/alert")
        self.assertIn("Используйте", self._last_message())

    def test_bindings_lists_user_channels(self) -> None:
        self.store.upsert_binding(_make_binding(user_id="42"))

        self._send("/bindings")
        reply = self._last_message()
        self.assertIn("telegram -> 500", reply)

    def test_templates_enumerates_available_templates(self) -> None:
        self._send("/templates")
        reply = self._last_message()
        self.assertIn("user_a_trader_position_updates", reply)
        self.assertIn("/create", reply)

    def test_history_shows_recent_attempts_scoped_to_user(self) -> None:
        attempt = DeliveryAttempt.model_validate(
            {
                "attempt_id": "att-1",
                "trigger_id": "tr-1",
                "alert_id": "a-1",
                "channel": DeliveryChannel.TELEGRAM,
                "destination": "500",
                "status": DeliveryStatus.SENT,
            }
        )
        self.attempt_store.save_for_user(attempt, user_id="42")
        foreign = attempt.model_copy(update={"attempt_id": "att-2"})
        self.attempt_store.save_for_user(foreign, user_id="99")

        self._send("/history 5")
        reply = self._last_message()
        self.assertIn("[sent]", reply)
        self.assertIn("a-1", reply)
        self.assertNotIn("att-2", reply)

    def test_history_rejects_invalid_n(self) -> None:
        self._send("/history abc")
        self.assertIn("Некорректное", self._last_message())

    def test_history_rejects_n_above_max(self) -> None:
        self._send("/history 999")
        self.assertIn("максимум 50", self._last_message())

    def test_history_rejects_signed_integer(self) -> None:
        self._send("/history +5")
        self.assertIn("Некорректное", self._last_message())

    def test_help_is_built_from_command_catalog(self) -> None:
        self._send("/help")
        reply = self._last_message()
        # Every registered slash command must appear in /help text so
        # the catalog stays the single source of truth.
        from alarm_system.api.routes.telegram_commands._registry import (
            COMMAND_CATALOG,
        )
        for entry in COMMAND_CATALOG:
            self.assertIn(f"/{entry.command}", reply)

    def test_status_reports_counts_and_mute_state(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)
        self.store.upsert_alert(
            _make_alert(alert_id="a-2", enabled=False),
            expected_version=0,
        )
        self.store.upsert_binding(_make_binding(user_id="42"))
        self.mute_store.set_mute(user_id="42", seconds=120)

        self._send("/status")
        reply = self._last_message()
        self.assertIn("алертов активно: 1 из 2", reply)
        self.assertIn("привязанных каналов: 1", reply)
        self.assertIn("тишина: активна", reply)
