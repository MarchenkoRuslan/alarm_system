from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from alarm_system.alert_store import (
    AlertStoreConflictError,
    InMemoryAlertStore,
)
from alarm_system.api.app import create_app
from alarm_system.api.rule_catalog import invalidate_rule_catalog_cache
from alarm_system.entities import (
    Alert,
    AlertType,
    DeliveryChannel,
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


class TelegramWriteCommandsTests(unittest.TestCase):
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

    def test_enable_disable_roundtrip_increments_version(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", enabled=False),
            expected_version=0,
        )

        self._send("/enable a-1")
        self.assertIn("включен", self._last_message())
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertTrue(alert.enabled)
        self.assertEqual(alert.version, 2)

        self._send("/disable a-1")
        self.assertIn("выключен", self._last_message())
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertFalse(alert.enabled)
        self.assertEqual(alert.version, 3)

    def test_enable_is_no_op_when_already_enabled(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)
        self._send("/enable a-1")
        self.assertIn("уже включен", self._last_message())

    def test_set_cooldown_validates_and_updates(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)

        self._send("/set_cooldown a-1 not-a-number")
        self.assertIn("Некорректное число", self._last_message())

        self._send("/set_cooldown a-1 120")
        self.assertIn("120s", self._last_message())
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertEqual(alert.cooldown_seconds, 120)

    def test_enable_of_missing_alert_returns_not_found(self) -> None:
        self._send("/enable missing")
        self.assertIn("не найден", self._last_message())

    def test_delete_requires_explicit_confirmation(self) -> None:
        self.store.upsert_alert(_make_alert(alert_id="a-1"), expected_version=0)

        self._send("/delete a-1")
        self.assertIn("Подтвердите", self._last_message())
        self.assertIsNotNone(self.store.get_alert("a-1"))

        self._send("/delete a-1 yes")
        self.assertIn("удален", self._last_message())
        self.assertIsNone(self.store.get_alert("a-1"))

    def test_delete_foreign_alert_is_not_found(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", user_id="other"),
            expected_version=0,
        )
        self._send("/delete a-1 yes")
        self.assertIn("не найден", self._last_message())
        self.assertIsNotNone(self.store.get_alert("a-1"))

    def test_create_from_template_forces_current_user(self) -> None:
        self._send(
            "/create rule-trader-position-default "
            "alert_id=a-created cooldown=90 enabled=true"
        )
        self.assertIn("a-created", self._last_message())
        alert = self.store.get_alert("a-created")
        assert alert is not None
        self.assertEqual(alert.user_id, "42")
        self.assertEqual(alert.cooldown_seconds, 90)
        self.assertTrue(alert.enabled)

    def test_create_from_template_defaults_to_enabled(self) -> None:
        self._send(
            "/create rule-trader-position-default alert_id=a-default"
        )
        alert = self.store.get_alert("a-default")
        assert alert is not None
        self.assertTrue(alert.enabled)
        # Explicit override still wins.
        self._send(
            "/create rule-trader-position-default "
            "alert_id=a-disabled enabled=false"
        )
        alert = self.store.get_alert("a-disabled")
        assert alert is not None
        self.assertFalse(alert.enabled)

    def test_create_from_rule_id_template_when_rules_path_set(self) -> None:
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

                store = InMemoryAlertStore()
                telegram = _FakeTelegramClient()
                app = create_app(
                    store=store,
                    telegram_client=telegram,
                    mute_store=InMemoryMuteStore(),
                    attempt_store=InMemoryDeliveryAttemptStore(),
                )
                client = TestClient(app)
                response = client.post(
                    "/webhooks/telegram",
                    json=_webhook_payload(
                        "/create rule-trader-position-default "
                        "alert_id=a-rule-template"
                    ),
                )
            self.assertEqual(response.status_code, 200)
            self.assertIn("a-rule-template", telegram.messages[-1][1])
            created = store.get_alert("a-rule-template")
            assert created is not None
            self.assertEqual(created.rule_id, "rule-trader-position-default")
            self.assertEqual(created.rule_version, 1)
        finally:
            if prev is None:
                os.environ.pop("ALARM_RULES_PATH", None)
            else:
                os.environ["ALARM_RULES_PATH"] = prev
            invalidate_rule_catalog_cache()

    def test_create_from_unknown_template(self) -> None:
        self._send("/create bogus")
        self.assertIn("Неизвестный шаблон", self._last_message())

    def test_create_duplicate_alert_id_rejected(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-dup"),
            expected_version=0,
        )
        self._send(
            "/create rule-trader-position-default alert_id=a-dup"
        )
        self.assertIn("уже существует", self._last_message())

    def test_create_raw_accepts_inline_json_and_overrides_user(self) -> None:
        payload = (
            '{"alert_id":"a-raw","rule_id":"rule-volume-spike-default","rule_version":1,'
            '"user_id":"will-be-ignored","alert_type":"volume_spike_5m",'
            '"filters_json":{},"cooldown_seconds":45,"enabled":true}'
        )
        self._send(f"/create_raw {payload}")
        self.assertIn("a-raw", self._last_message())
        alert = self.store.get_alert("a-raw")
        assert alert is not None
        self.assertEqual(alert.user_id, "42")
        self.assertEqual(alert.cooldown_seconds, 45)

    def test_create_raw_rejects_invalid_json(self) -> None:
        self._send("/create_raw {not-json}")
        self.assertIn("Не удалось прочитать JSON", self._last_message())

    def test_create_rejects_unknown_rule_identity_when_whitelist_set(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            rules_path = Path(tmp_dir) / "rules.json"
            rules_path.write_text(
                '[{"rule_id":"r-existing","tenant_id":"tenant-a","name":"rule",'
                '"rule_type":"volume_spike_5m","version":1,'
                '"expression":{"signal":"price_return_1m_pct","op":"gte",'
                '"threshold":1.0,"window":{"size_seconds":60,"slide_seconds":10}}}]',
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"ALARM_RULES_PATH": str(rules_path)},
                clear=False,
            ):
                app = create_app(
                    store=self.store,
                    telegram_client=self.telegram,
                    mute_store=self.mute_store,
                    attempt_store=self.attempt_store,
                )
                client = TestClient(app)
                raw_payload = (
                    '{"alert_id":"a-bad-rule","rule_id":"r-missing",'
                    '"rule_version":1,"user_id":"42",'
                    '"alert_type":"volume_spike_5m","filters_json":{},'
                    '"cooldown_seconds":60,"enabled":true}'
                )
                response = client.post(
                    "/webhooks/telegram",
                    json={
                        "update_id": 1,
                        "message": {
                            "text": f"/create_raw {raw_payload}",
                            "chat": {"id": 500},
                            "from": {"id": 42},
                        },
                    },
                )
                self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.get_alert("a-bad-rule"))
        self.assertTrue(
            any(
                "r-missing" in message[1] and "не зарегистрировано" in message[1]
                for message in self.telegram.messages
            )
        )

    def test_optimistic_conflict_surfaced_as_friendly_message(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", enabled=False),
            expected_version=0,
        )

        original_upsert = self.store.upsert_alert

        def conflicting_upsert(alert, *, expected_version):
            raise AlertStoreConflictError("simulated conflict")

        self.store.upsert_alert = conflicting_upsert  # type: ignore[method-assign]
        try:
            self._send("/enable a-1")
        finally:
            self.store.upsert_alert = original_upsert  # type: ignore[method-assign]
        self.assertIn("изменен параллельно", self._last_message())
