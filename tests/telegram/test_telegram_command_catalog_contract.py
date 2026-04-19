from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from alarm_system.alert_store import InMemoryAlertStore
from alarm_system.api.app import create_app
from alarm_system.api.routes.telegram_commands._registry import COMMAND_CATALOG
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
    alert_id: str,
    enabled: bool,
    user_id: str = "42",
) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": alert_id,
            "rule_id": "r-1",
            "rule_version": 1,
            "user_id": user_id,
            "alert_type": AlertType.VOLUME_SPIKE_5M,
            "filters_json": {},
            "cooldown_seconds": 60,
            "channels": [DeliveryChannel.TELEGRAM],
            "enabled": enabled,
        }
    )


def _webhook_payload(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": 500},
            "from": {"id": 42},
        },
    }


class TelegramCommandCatalogContractTests(unittest.TestCase):
    """Contract guard for command coverage.

    Every command declared in ``COMMAND_CATALOG`` must have at least one
    webhook integration scenario in tests. This prevents silent drift
    when a new command is added to the catalog but forgotten in tests.
    """

    _COMMAND_SCENARIOS: dict[str, str] = {
        "start": "/start",
        "stop": "/stop",
        "help": "/help",
        "status": "/status",
        "new": "/new",
        "alerts": "/alerts",
        "alert": "/alert cmd-on",
        "bindings": "/bindings",
        "history": "/history 1",
        "templates": "/templates",
        "enable": "/enable cmd-off",
        "disable": "/disable cmd-on",
        "set_cooldown": "/set_cooldown cmd-on 120",
        "set_filters": "/set_filters cmd-on liquidity_usd_min=100000",
        "delete": "/delete cmd-delete yes",
        "mute": "/mute 30m",
        "unmute": "/unmute",
        "create": "/create user_a_trader_position_updates alert_id=catalog-create",
        "create_raw": (
            '/create_raw {"alert_id":"catalog-raw","rule_id":"r-raw",'
            '"rule_version":1,"user_id":"42","alert_type":"volume_spike_5m",'
            '"filters_json":{},"cooldown_seconds":60,"enabled":true}'
        ),
    }

    def _build_harness(
        self,
        *,
        command: str,
    ) -> tuple[TestClient, _FakeTelegramClient]:
        store = InMemoryAlertStore()
        telegram = _FakeTelegramClient()
        mute_store = InMemoryMuteStore()
        attempts = InMemoryDeliveryAttemptStore()

        store.upsert_alert(
            _make_alert(alert_id="cmd-on", enabled=True),
            expected_version=0,
        )
        store.upsert_alert(
            _make_alert(alert_id="cmd-off", enabled=False),
            expected_version=0,
        )
        store.upsert_alert(
            _make_alert(alert_id="cmd-delete", enabled=True),
            expected_version=0,
        )
        store.upsert_binding(
            ChannelBinding.model_validate(
                {
                    "binding_id": "tg-42-500",
                    "user_id": "42",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "500",
                    "is_verified": True,
                }
            )
        )
        attempts.save_for_user(
            DeliveryAttempt.model_validate(
                {
                    "attempt_id": "hist-1",
                    "trigger_id": "tr-1",
                    "alert_id": "cmd-on",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "500",
                    "status": DeliveryStatus.SENT,
                }
            ),
            user_id="42",
        )
        if command == "unmute":
            mute_store.set_mute(user_id="42", seconds=60)

        app = create_app(
            store=store,
            telegram_client=telegram,
            mute_store=mute_store,
            attempt_store=attempts,
        )
        return TestClient(app), telegram

    def test_catalog_and_scenarios_are_in_sync(self) -> None:
        catalog_commands = {spec.command for spec in COMMAND_CATALOG}
        scenario_commands = set(self._COMMAND_SCENARIOS)
        self.assertSetEqual(
            catalog_commands,
            scenario_commands,
            "Every COMMAND_CATALOG entry must have a webhook scenario "
            "and no stale scenarios may remain.",
        )

    def test_every_catalog_command_has_webhook_integration_scenario(self) -> None:
        for command in sorted(self._COMMAND_SCENARIOS):
            with self.subTest(command=command):
                client, telegram = self._build_harness(command=command)
                response = client.post(
                    "/webhooks/telegram",
                    json=_webhook_payload(self._COMMAND_SCENARIOS[command]),
                )
                self.assertEqual(response.status_code, 200)
                self.assertGreaterEqual(
                    len(telegram.messages),
                    1,
                    f"Command '{command}' did not emit a bot response.",
                )
                self.assertEqual(telegram.messages[-1][0], "500")
