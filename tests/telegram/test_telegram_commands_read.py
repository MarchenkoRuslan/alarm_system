from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.api.rule_catalog import invalidate_rule_catalog_cache
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
        repo_root = Path(__file__).resolve().parent.parent.parent
        self._prev_rules_path = os.environ.get("ALARM_RULES_PATH")
        os.environ["ALARM_RULES_PATH"] = str(
            repo_root / "deploy" / "config" / "rules.sample.json"
        )
        invalidate_rule_catalog_cache()
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

    def tearDown(self) -> None:
        if self._prev_rules_path is None:
            os.environ.pop("ALARM_RULES_PATH", None)
        else:
            os.environ["ALARM_RULES_PATH"] = self._prev_rules_path
        invalidate_rule_catalog_cache()

    def _last_message(self) -> str:
        self.assertTrue(self.telegram.messages, "bot did not reply")
        return self.telegram.messages[-1][1]

    def _send(self, text: str) -> None:
        response = self.client.post(
            "/webhooks/telegram",
            json=_webhook_payload(text),
        )
        self.assertEqual(response.status_code, 200)

    def test_alerts_hides_disabled_by_default(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)
        disabled = _make_alert(alert_id="a-2", enabled=False)
        self.store.upsert_alert(disabled, expected_version=0)

        # Default /alerts renders an interactive keyboard; alert ids live
        # in the session store, not in the text. The text reports totals.
        self._send("/alerts")
        reply = self._last_message()
        self.assertIn("всего 1", reply)

    def test_alerts_empty_state_mentions_wizard(self) -> None:
        self._send("/alerts")
        reply = self._last_message()
        self.assertIn("Создать алерт", reply)

    def test_alert_command_shows_details_only_to_owner(self) -> None:
        owned = _make_alert(alert_id="a-owned", user_id="42", cooldown=30)
        foreign = _make_alert(alert_id="a-foreign", user_id="99")
        self.store.upsert_alert(owned, expected_version=0)
        self.store.upsert_alert(foreign, expected_version=0)

        self._send("/alert a-owned")
        reply = self._last_message()
        self.assertIn("a-owned", reply)
        self.assertIn("Cooldown: 30s", reply)

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
        self.assertIn("rule-trader-position-default", reply)
        self.assertIn("/create", reply)

    def test_templates_with_rules_path_include_rule_id_templates(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        prev = os.environ.get("ALARM_RULES_PATH")
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                rules_path = Path(tmp_dir) / "rules.json"
                shutil.copy(
                    repo_root / "deploy" / "config" / "rules.sample.json",
                    rules_path,
                )
                os.environ["ALARM_RULES_PATH"] = str(rules_path)
                invalidate_rule_catalog_cache()
                self._send("/templates")
        finally:
            if prev is None:
                os.environ.pop("ALARM_RULES_PATH", None)
            else:
                os.environ["ALARM_RULES_PATH"] = prev
            invalidate_rule_catalog_cache()
        reply = self._last_message()
        self.assertIn("rule-trader-position-default", reply)
        self.assertIn("rule-volume-spike-default", reply)
        self.assertIn("/create", reply)

    def test_templates_example_stays_valid_for_partial_rule_catalog(self) -> None:
        prev = os.environ.get("ALARM_RULES_PATH")
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                rules_path = Path(tmp_dir) / "rules.json"
                rules_path.write_text(
                    '[{"rule_id":"rule-volume-only","tenant_id":"tenant-a",'
                    '"name":"Volume only","rule_type":"volume_spike_5m",'
                    '"version":1,"expression":{"signal":"price_return_1m_pct",'
                    '"op":"gte","threshold":1.0,"window":{"size_seconds":60,'
                    '"slide_seconds":10}}}]',
                    encoding="utf-8",
                )
                os.environ["ALARM_RULES_PATH"] = str(rules_path)
                invalidate_rule_catalog_cache()
                self._send("/templates")
        finally:
            if prev is None:
                os.environ.pop("ALARM_RULES_PATH", None)
            else:
                os.environ["ALARM_RULES_PATH"] = prev
            invalidate_rule_catalog_cache()
        reply = self._last_message()
        self.assertNotIn("rule-trader-position-default", reply)
        self.assertIn("rule-volume-only", reply)
        self.assertIn("/create rule-volume-only cooldown=120", reply)

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
