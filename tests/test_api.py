from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreContractError,
    InMemoryAlertStore,
)
from alarm_system.api.app import _store_from_env, create_app
from alarm_system.entities import Alert


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.webhook_registrations: list[tuple[str, str | None]] = []

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))

    async def set_webhook(
        self,
        *,
        webhook_url: str,
        secret_token: str | None = None,
    ) -> dict[str, object]:
        self.webhook_registrations.append((webhook_url, secret_token))
        return {"ok": True, "result": True}


class _FailingWebhookTelegramClient(_FakeTelegramClient):
    async def set_webhook(
        self,
        *,
        webhook_url: str,
        secret_token: str | None = None,
    ) -> dict[str, object]:
        raise RuntimeError("telegram api timeout")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAlertStore()
        self.telegram = _FakeTelegramClient()
        app = create_app(store=self.store, telegram_client=self.telegram)
        self.client = TestClient(app)

    def test_internal_alert_create_update_and_conflicts(self) -> None:
        created = self.client.post(
            "/internal/alerts",
            json={
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
                "cooldown_seconds": 60,
                "channels": ["telegram"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["alert"]["version"], 1)
        created_at_before_update = created.json()["alert"]["created_at"]

        conflict = self.client.post(
            "/internal/alerts",
            json={
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            },
        )
        self.assertEqual(conflict.status_code, 409)

        update_ok = self.client.put(
            "/internal/alerts/a-1",
            json={
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
                "expected_version": 1,
            },
        )
        self.assertEqual(update_ok.status_code, 200)
        self.assertEqual(update_ok.json()["alert"]["version"], 2)
        self.assertEqual(
            update_ok.json()["alert"]["created_at"],
            created_at_before_update,
        )

        update_conflict = self.client.put(
            "/internal/alerts/a-1",
            json={
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
                "expected_version": 1,
            },
        )
        self.assertEqual(update_conflict.status_code, 409)

        update_without_version = self.client.put(
            "/internal/alerts/a-1",
            json={
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            },
        )
        self.assertEqual(update_without_version.status_code, 422)

        update_missing = self.client.put(
            "/internal/alerts/a-missing",
            json={
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
                "expected_version": 1,
            },
        )
        self.assertEqual(update_missing.status_code, 404)

        listed = self.client.get("/internal/alerts?user_id=u-1")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["alerts"]), 1)

    def test_webhook_start_and_alerts_command(self) -> None:
        start = self.client.post(
            "/webhooks/telegram",
            json={
                "update_id": 1,
                "message": {
                    "text": "/start",
                    "chat": {"id": 500},
                    "from": {"id": 42},
                },
            },
        )
        self.assertEqual(start.status_code, 200)
        self.assertEqual(len(self.store.list_bindings(user_id="42")), 1)

        self.client.post(
            "/internal/alerts",
            json={
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "42",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            },
        )
        alerts_command = self.client.post(
            "/webhooks/telegram",
            json={
                "update_id": 2,
                "message": {
                    "text": "/alerts",
                    "chat": {"id": 500},
                    "from": {"id": 42},
                },
            },
        )
        self.assertEqual(alerts_command.status_code, 200)
        self.assertTrue(
            any("Ваши активные алерты" in message[1] for message in self.telegram.messages)
        )

    def test_api_startup_registers_webhook_when_env_present(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALARM_TELEGRAM_WEBHOOK_URL": "https://example.com/webhooks/telegram",
                "ALARM_TELEGRAM_WEBHOOK_SECRET": "secret-1",
            },
            clear=False,
        ):
            app = create_app(store=InMemoryAlertStore(), telegram_client=self.telegram)
            with TestClient(app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.telegram.webhook_registrations,
            [("https://example.com/webhooks/telegram", "secret-1")],
        )

    def test_api_startup_is_fail_open_when_webhook_registration_fails(self) -> None:
        failing_client = _FailingWebhookTelegramClient()
        with patch.dict(
            "os.environ",
            {"ALARM_TELEGRAM_WEBHOOK_URL": "https://example.com/webhooks/telegram"},
            clear=False,
        ), patch("alarm_system.api.app.logger.error") as logger_error:
            app = create_app(store=InMemoryAlertStore(), telegram_client=failing_client)
            with TestClient(app) as client:
                response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        logger_error.assert_called_once()

    def test_webhook_rejects_requests_with_invalid_secret(self) -> None:
        with patch.dict(
            "os.environ",
            {"ALARM_TELEGRAM_WEBHOOK_SECRET": "secret-1"},
            clear=False,
        ):
            app = create_app(store=InMemoryAlertStore(), telegram_client=self.telegram)
            client = TestClient(app)
            response = client.post(
                "/webhooks/telegram",
                json={
                    "update_id": 1,
                    "message": {
                        "text": "/help",
                        "chat": {"id": 500},
                        "from": {"id": 42},
                    },
                },
            )
        self.assertEqual(response.status_code, 401)

    def test_webhook_accepts_requests_with_valid_secret(self) -> None:
        with patch.dict(
            "os.environ",
            {"ALARM_TELEGRAM_WEBHOOK_SECRET": "secret-1"},
            clear=False,
        ):
            app = create_app(store=InMemoryAlertStore(), telegram_client=self.telegram)
            client = TestClient(app)
            response = client.post(
                "/webhooks/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret-1"},
                json={
                    "update_id": 1,
                    "message": {
                        "text": "/help",
                        "chat": {"id": 500},
                        "from": {"id": 42},
                    },
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_validation_error_handler_logs_details(self) -> None:
        with patch("alarm_system.api.app.logger.warning") as logger_warning:
            response = self.client.get("/internal/alerts?include_disabled=not_bool")
        self.assertEqual(response.status_code, 422)
        self.assertIn("detail", response.json())
        logger_warning.assert_called_once()

    def test_store_from_env_applies_sql_migrations_when_enabled(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALARM_POSTGRES_DSN": "postgresql://localhost/test",
                "ALARM_REDIS_URL": "redis://localhost:6379/0",
                "ALARM_AUTO_APPLY_SQL_MIGRATIONS": "true",
                "ALARM_ENV": "prod",
            },
            clear=False,
        ):
            with patch(
                "alarm_system.api.app.apply_sql_migrations"
            ) as apply_migrations, patch(
                "alarm_system.api.app.build_cached_alert_store",
                return_value=InMemoryAlertStore(),
            ), patch(
                "alarm_system.api.app._build_redis_client",
                return_value=object(),
            ):
                _store_from_env()
        apply_migrations.assert_called_once()

    def test_store_from_env_allows_in_memory_only_in_dev_test(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALARM_POSTGRES_DSN": "",
                "ALARM_REDIS_URL": "",
                "ALARM_ENV": "dev",
            },
            clear=False,
        ):
            self.assertIsInstance(_store_from_env(), InMemoryAlertStore)

        with patch.dict(
            "os.environ",
            {
                "ALARM_POSTGRES_DSN": "",
                "ALARM_REDIS_URL": "",
                "ALARM_ENV": "test",
            },
            clear=False,
        ):
            self.assertIsInstance(_store_from_env(), InMemoryAlertStore)

    def test_store_from_env_fails_without_postgres_in_prod_like_env(self) -> None:
        for env in ("staging", "prod"):
            with patch.dict(
                "os.environ",
                {
                    "ALARM_POSTGRES_DSN": "",
                    "ALARM_REDIS_URL": "",
                    "ALARM_ENV": env,
                },
                clear=False,
            ):
                with self.assertRaises(RuntimeError):
                    _store_from_env()

    def test_store_from_env_rejects_invalid_alarm_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALARM_POSTGRES_DSN": "",
                "ALARM_REDIS_URL": "",
                "ALARM_ENV": "sandbox",
            },
            clear=False,
        ):
            with self.assertRaises(ValueError):
                _store_from_env()

    def test_alert_routes_return_503_on_backend_errors(self) -> None:
        with patch.object(
            self.store,
            "list_alerts",
            side_effect=AlertStoreBackendError("backend down"),
        ):
            response = self.client.get("/internal/alerts")
        self.assertEqual(response.status_code, 503)

        with patch.object(
            self.store,
            "get_alert",
            side_effect=AlertStoreBackendError("backend down"),
        ):
            response = self.client.get("/internal/alerts/a-1")
        self.assertEqual(response.status_code, 503)

        with patch.object(
            self.store,
            "delete_alert",
            side_effect=AlertStoreBackendError("backend down"),
        ):
            response = self.client.delete("/internal/alerts/a-1")
        self.assertEqual(response.status_code, 503)

    def test_alert_create_and_update_return_422_on_contract_errors(self) -> None:
        with patch.object(self.store, "get_alert", return_value=None), patch.object(
            self.store,
            "upsert_alert",
            side_effect=AlertStoreContractError("bad contract"),
        ):
            create_resp = self.client.post(
                "/internal/alerts",
                json={
                    "alert_id": "a-1",
                    "rule_id": "r-1",
                    "rule_version": 1,
                    "user_id": "u-1",
                    "alert_type": "volume_spike_5m",
                    "filters_json": {},
                },
            )
        self.assertEqual(create_resp.status_code, 422)

        existing_alert = Alert.model_validate(
            {
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            }
        )
        with patch.object(
            self.store,
            "get_alert",
            return_value=existing_alert,
        ), patch.object(
            self.store,
            "upsert_alert",
            side_effect=AlertStoreContractError("bad contract"),
        ):
            update_resp = self.client.put(
                "/internal/alerts/a-1",
                json={
                    "rule_id": "r-1",
                    "rule_version": 1,
                    "user_id": "u-1",
                    "alert_type": "volume_spike_5m",
                    "filters_json": {},
                    "expected_version": 1,
                },
            )
        self.assertEqual(update_resp.status_code, 422)

    def test_channel_binding_routes_404_and_503_paths(self) -> None:
        not_found = self.client.get("/internal/channel-bindings/missing")
        self.assertEqual(not_found.status_code, 404)

        with patch.object(
            self.store,
            "list_bindings",
            side_effect=AlertStoreBackendError("backend down"),
        ):
            list_resp = self.client.get("/internal/channel-bindings")
        self.assertEqual(list_resp.status_code, 503)

        with patch.object(
            self.store,
            "upsert_binding",
            side_effect=AlertStoreBackendError("backend down"),
        ):
            create_resp = self.client.post(
                "/internal/channel-bindings",
                json={
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": "telegram",
                    "destination": "123",
                    "is_verified": True,
                },
            )
        self.assertEqual(create_resp.status_code, 503)
