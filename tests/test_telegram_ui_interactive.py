"""Integration tests for the interactive Telegram UX.

Scenarios covered:

- ``/start`` and ``/alerts`` attach an inline keyboard.
- ``/new`` starts the create-alert wizard and stores its state in the
  session store.
- A sequence of ``callback_query`` updates walks through the wizard
  and ends up creating an alert via the same pipeline as the slash
  ``/create`` command.
- Alert card callbacks (enable/disable/cooldown) round-trip through
  the optimistic-locking store the way the ``/enable`` command does.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.api.routes.telegram_commands._callbacks import (
    SESSION_TTL_SECONDS,
)
from alarm_system.entities import (
    Alert,
    AlertType,
    DeliveryChannel,
)
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
    InMemorySessionStore,
)

from tests.test_api import _FakeTelegramClient


def _make_alert(
    *,
    alert_id: str,
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


def _text(text: str, *, user_id: int = 42, chat_id: int = 500) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "text": text,
            "chat": {"id": chat_id},
            "from": {"id": user_id},
        },
    }


def _callback(data: str, *, user_id: int = 42, chat_id: int = 500) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {"id": user_id},
            "message": {
                "message_id": 42,
                "chat": {"id": chat_id},
            },
        },
    }


class InteractiveUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAlertStore()
        self.telegram = _FakeTelegramClient()
        self.mute_store = InMemoryMuteStore()
        self.attempt_store = InMemoryDeliveryAttemptStore()
        # Explicit session store so the tests can inspect wizard
        # state across individual webhook requests.
        self.session_store = InMemorySessionStore()
        self.app = create_app(
            store=self.store,
            telegram_client=self.telegram,
            mute_store=self.mute_store,
            attempt_store=self.attempt_store,
            session_store=self.session_store,
        )
        self.client = TestClient(self.app)

    def _post(self, payload: dict) -> None:
        response = self.client.post("/webhooks/telegram", json=payload)
        self.assertEqual(response.status_code, 200)

    def test_start_attaches_home_keyboard(self) -> None:
        self._post(_text("/start"))
        self.assertEqual(len(self.telegram.messages), 1)
        markup = self.telegram.reply_markups[-1]
        self.assertIsNotNone(markup)
        assert markup is not None
        labels = [
            btn["text"]
            for row in markup["inline_keyboard"]
            for btn in row
        ]
        self.assertIn("Мои алерты", labels)
        self.assertIn("Создать алерт", labels)

    def test_alerts_attaches_list_keyboard_with_navigation(self) -> None:
        for idx in range(3):
            self.store.upsert_alert(
                _make_alert(alert_id=f"a-{idx}"),
                expected_version=0,
            )
        self._post(_text("/alerts"))
        markup = self.telegram.reply_markups[-1]
        assert markup is not None
        # Every alert gets a row plus a nav row + a create + a home row.
        row_count = len(markup["inline_keyboard"])
        self.assertGreaterEqual(row_count, 3 + 1)

    def test_new_starts_wizard_and_persists_state(self) -> None:
        self._post(_text("/new"))
        state = self.session_store.load("42")
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["wizard"]["step"], "scenario")

    def test_wizard_happy_path_creates_alert(self) -> None:
        self._post(_text("/new"))
        self._post(_callback("v1:wz_scn:trader_positions"))
        self._post(_callback("v1:wz_sens:balanced"))
        self._post(_callback("v1:wz_cd:120"))
        self._post(_callback("v1:wz_confirm"))
        alerts = self.store.list_alerts(user_id="42", include_disabled=True)
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(
            alert.alert_type,
            AlertType.TRADER_POSITION_UPDATE,
        )
        self.assertEqual(alert.cooldown_seconds, 120)
        self.assertTrue(alert.enabled)
        self.assertIsNone(self.session_store.load("42"))

    def test_wizard_cancel_clears_session(self) -> None:
        self._post(_text("/new"))
        self._post(_callback("v1:wz_scn:volume_spike"))
        self._post(_callback("v1:wz_cancel"))
        self.assertIsNone(self.session_store.load("42"))

    def test_alert_card_toggle_roundtrip(self) -> None:
        # Use an *enabled* alert so it appears in the default active list.
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", enabled=True),
            expected_version=0,
        )
        # Default /alerts shows only active alerts — consistent with
        # the "К списку" callback path.  "00" maps to "a-1".
        self._post(_callback("v1:alerts:0"))
        # Disable via card.
        self._post(_callback("v1:alert_disable:00"))
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertFalse(alert.enabled)
        # Re-enable via /alert slash command, which uses the same
        # session-token mechanism and also works for disabled alerts.
        self._post(_text("/alert a-1"))
        self._post(_callback("v1:alert_enable:00"))
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertTrue(alert.enabled)

    def test_callback_list_shows_only_active_alerts(self) -> None:
        """The inline keyboard list and the /alerts slash command are consistent."""

        self.store.upsert_alert(
            _make_alert(alert_id="a-active", enabled=True),
            expected_version=0,
        )
        self.store.upsert_alert(
            _make_alert(alert_id="a-disabled", enabled=False),
            expected_version=0,
        )
        # Inline callback list: only active.
        self._post(_callback("v1:alerts:0"))
        reply = self.telegram.edits[-1][2]  # (chat_id, msg_id, text, markup)
        self.assertIn("всего 1", reply)
        self.assertNotIn("a-disabled", reply)

    def test_alert_card_cooldown_preset(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", cooldown=60),
            expected_version=0,
        )
        self._post(_text("/alerts"))
        self._post(_callback("v1:alert_cd_set:00:300"))
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertEqual(alert.cooldown_seconds, 300)

    def test_alert_card_delete_flow(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1"),
            expected_version=0,
        )
        self._post(_text("/alerts"))
        # First click asks for confirmation.
        self._post(_callback("v1:alert_del:00"))
        self.assertIsNotNone(self.store.get_alert("a-1"))
        # Confirm click actually deletes.
        self._post(_callback("v1:alert_del_yes:00"))
        self.assertIsNone(self.store.get_alert("a-1"))

    def test_unknown_callback_gets_graceful_toast(self) -> None:
        self._post(_callback("legacy:whatever"))
        self.assertTrue(self.telegram.callback_answers)
        _cb_id, text, _show = self.telegram.callback_answers[-1]
        self.assertIsNotNone(text)

    def test_pending_cooldown_input_from_message(self) -> None:
        self.store.upsert_alert(
            _make_alert(alert_id="a-1", cooldown=60),
            expected_version=0,
        )
        self._post(_text("/alerts"))
        # Request custom cooldown input slot.
        self._post(_callback("v1:alert_cd_custom:00"))
        # Now a plain text number should be consumed by the pending slot.
        self._post(_text("240"))
        alert = self.store.get_alert("a-1")
        assert alert is not None
        self.assertEqual(alert.cooldown_seconds, 240)


    def test_wizard_confirm_failure_preserves_session(self) -> None:
        """Failed creation must not clear wizard state."""

        from unittest.mock import patch
        from alarm_system.alert_store import AlertStoreConflictError

        # Walk the wizard to the preview step.
        self._post(_text("/new"))
        self._post(_callback("v1:wz_scn:trader_positions"))
        self._post(_callback("v1:wz_sens:balanced"))
        self._post(_callback("v1:wz_cd:60"))
        # Confirm should reach the store — simulate a conflict error so
        # the creation fails.
        with patch.object(
            self.store,
            "upsert_alert",
            side_effect=AlertStoreConflictError("conflict"),
        ):
            self._post(_callback("v1:wz_confirm"))

        # Alert must NOT have been created.
        alerts = self.store.list_alerts(user_id="42", include_disabled=True)
        self.assertEqual(len(alerts), 0)

        # Session must still be alive so the user can retry.
        session = self.session_store.load("42")
        self.assertIsNotNone(session)
        assert session is not None
        # The wizard state should still be at "preview".
        self.assertEqual(session["wizard"]["step"], "preview")

        # The callback must have been answered (no toast of "Алерт создан").
        self.assertTrue(
            self.telegram.callback_answers,
            "dispatcher must always answer callback queries",
        )
        _cb_id, toast_text, _show = self.telegram.callback_answers[-1]
        self.assertNotEqual(toast_text, "Алерт создан")

    def test_wizard_confirm_success_clears_session(self) -> None:
        """Bug-2 regression: successful creation clears wizard state and toasts."""

        self._post(_text("/new"))
        self._post(_callback("v1:wz_scn:volume_spike"))
        self._post(_callback("v1:wz_sens:conservative"))
        self._post(_callback("v1:wz_cd:300"))
        self._post(_callback("v1:wz_confirm"))

        # Alert must have been created.
        alerts = self.store.list_alerts(user_id="42", include_disabled=True)
        self.assertEqual(len(alerts), 1)

        # Session must be cleared.
        self.assertIsNone(self.session_store.load("42"))

        # Toast must announce success.
        self.assertTrue(self.telegram.callback_answers)
        _cb_id, toast_text, _show = self.telegram.callback_answers[-1]
        self.assertEqual(toast_text, "Алерт создан")


class WizardSessionTTLTests(unittest.TestCase):
    def test_session_ttl_matches_spec(self) -> None:
        # The in-memory session store itself doesn't schedule expiry
        # but the wizard passes this TTL every time it saves state.
        self.assertEqual(SESSION_TTL_SECONDS, 10 * 60)
