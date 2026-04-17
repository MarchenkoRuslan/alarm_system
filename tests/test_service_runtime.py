from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from alarm_system.entities import Alert
from alarm_system.rules_dsl import AlertRuleV1
from alarm_system.service_runtime import (
    ServiceRuntimeConfig,
    _build_config,
    _build_rule_bindings,
    _load_runtime_alert_config,
    _verify_redis_connectivity,
)


def _write_json_array(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")


class ServiceRuntimeConfigTests(unittest.TestCase):
    def test_from_env_accepts_dry_run_without_telegram_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            rules_path = root / "rules.json"
            alerts_path = root / "alerts.json"
            bindings_path = root / "bindings.json"
            _write_json_array(rules_path, "[]")
            _write_json_array(alerts_path, "[]")
            _write_json_array(bindings_path, "[]")
            env = {
                "ALARM_ASSET_IDS": "asset-1,asset-2",
                "ALARM_RULES_PATH": str(rules_path),
                "ALARM_ALERTS_PATH": str(alerts_path),
                "ALARM_CHANNEL_BINDINGS_PATH": str(bindings_path),
                "ALARM_REDIS_URL": "redis://localhost:6379/0",
                "ALARM_EXECUTE_SENDS": "false",
            }
            original = {name: os.getenv(name) for name in env}
            try:
                for name, value in env.items():
                    os.environ[name] = value
                cfg = ServiceRuntimeConfig.from_env()
            finally:
                for name, old in original.items():
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old

        self.assertFalse(cfg.execute_sends)
        self.assertEqual(cfg.asset_ids, ["asset-1", "asset-2"])

    def test_from_env_requires_telegram_token_in_live_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            rules_path = root / "rules.json"
            alerts_path = root / "alerts.json"
            bindings_path = root / "bindings.json"
            _write_json_array(rules_path, "[]")
            _write_json_array(alerts_path, "[]")
            _write_json_array(bindings_path, "[]")
            env = {
                "ALARM_ASSET_IDS": "asset-1",
                "ALARM_RULES_PATH": str(rules_path),
                "ALARM_ALERTS_PATH": str(alerts_path),
                "ALARM_CHANNEL_BINDINGS_PATH": str(bindings_path),
                "ALARM_REDIS_URL": "redis://localhost:6379/0",
                "ALARM_EXECUTE_SENDS": "true",
            }
            original = {name: os.getenv(name) for name in env}
            try:
                for name, value in env.items():
                    os.environ[name] = value
                with self.assertRaises(ValidationError):
                    ServiceRuntimeConfig.from_env()
            finally:
                for name, old in original.items():
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old

    def test_build_config_dry_run_overrides_execute_sends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            rules_path = root / "rules.json"
            alerts_path = root / "alerts.json"
            bindings_path = root / "bindings.json"
            _write_json_array(rules_path, "[]")
            _write_json_array(alerts_path, "[]")
            _write_json_array(bindings_path, "[]")
            env = {
                "ALARM_ASSET_IDS": "asset-1",
                "ALARM_RULES_PATH": str(rules_path),
                "ALARM_ALERTS_PATH": str(alerts_path),
                "ALARM_CHANNEL_BINDINGS_PATH": str(bindings_path),
                "ALARM_REDIS_URL": "redis://localhost:6379/0",
                "ALARM_EXECUTE_SENDS": "true",
            }
            original = {name: os.getenv(name) for name in env}
            try:
                for name, value in env.items():
                    os.environ[name] = value
                args = argparse.Namespace(dry_run=True)
                cfg = _build_config(args)
            finally:
                for name, old in original.items():
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old

        self.assertFalse(cfg.execute_sends)
        self.assertIsNone(cfg.telegram_bot_token)

    def test_database_config_requires_postgres_dsn(self) -> None:
        with self.assertRaises(ValidationError):
            ServiceRuntimeConfig.model_validate(
                {
                    "asset_ids": ["asset-1"],
                    "rules_path": "rules.json",
                    "alerts_path": "alerts.json",
                    "channel_bindings_path": "bindings.json",
                    "redis_url": "redis://localhost:6379/0",
                    "use_database_config": True,
                    "execute_sends": False,
                }
            )


class RedisStartupCheckTests(unittest.TestCase):
    def test_verify_redis_connectivity_raises_fail_fast_and_masks_password(
        self,
    ) -> None:
        class _FailingRedis:
            def ping(self) -> bool:
                raise ConnectionError("boom")

        with self.assertRaises(RuntimeError) as ctx:
            _verify_redis_connectivity(
                _FailingRedis(),
                "redis://user:secret@localhost:6379/0",
            )
        message = str(ctx.exception)
        self.assertIn("***", message)
        self.assertNotIn("secret", message)

    def test_verify_redis_connectivity_accepts_true_response(self) -> None:
        class _HealthyRedis:
            def ping(self) -> bool:
                return True

        _verify_redis_connectivity(
            _HealthyRedis(),
            "redis://localhost:6379/0",
        )


class RuleBindingBuildTests(unittest.TestCase):
    def test_build_rule_bindings_links_alert_to_rule_identity(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-1",
                "tenant_id": "tenant-a",
                "name": "rule-one",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_1m_pct",
                    "op": "gte",
                    "threshold": 1.0,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
            }
        )
        alert = Alert.model_validate(
            {
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            }
        )

        bindings, alert_by_id = _build_rule_bindings([rule], [alert])

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].alert_id, "a-1")
        self.assertEqual(bindings[0].rule.rule_id, "r-1")
        self.assertEqual(alert_by_id["a-1"].user_id, "u-1")

    def test_build_rule_bindings_rejects_unknown_rule_identity(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-1",
                "tenant_id": "tenant-a",
                "name": "rule-one",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_1m_pct",
                    "op": "gte",
                    "threshold": 1.0,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
            }
        )
        alert = Alert.model_validate(
            {
                "alert_id": "a-1",
                "rule_id": "r-missing",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            }
        )

        with self.assertRaises(ValueError) as ctx:
            _build_rule_bindings([rule], [alert])
        self.assertIn("count=1", str(ctx.exception))
        self.assertIn("a-1 -> r-missing#1", str(ctx.exception))


class RuntimeConfigSourceTests(unittest.TestCase):
    def test_load_runtime_alert_config_uses_json_when_database_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            alerts_path = root / "alerts.json"
            bindings_path = root / "bindings.json"
            alerts_path.write_text(
                '[{"alert_id":"a-1","rule_id":"r-1","rule_version":1,'
                '"user_id":"u-1","alert_type":"volume_spike_5m",'
                '"filters_json":{},"enabled":true}]',
                encoding="utf-8",
            )
            bindings_path.write_text(
                '[{"binding_id":"b-1","user_id":"u-1","channel":"telegram",'
                '"destination":"123","is_verified":true}]',
                encoding="utf-8",
            )
            config = ServiceRuntimeConfig.model_validate(
                {
                    "asset_ids": ["asset-1"],
                    "rules_path": "rules.json",
                    "alerts_path": str(alerts_path),
                    "channel_bindings_path": str(bindings_path),
                    "redis_url": "redis://localhost:6379/0",
                    "execute_sends": False,
                }
            )
            alerts, bindings = _load_runtime_alert_config(
                config,
                redis_client=object(),
            )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(bindings), 1)

    def test_load_runtime_alert_config_uses_cached_store_when_enabled(
        self,
    ) -> None:
        config = ServiceRuntimeConfig.model_validate(
            {
                "asset_ids": ["asset-1"],
                "rules_path": "rules.json",
                "alerts_path": "alerts.json",
                "channel_bindings_path": "bindings.json",
                "redis_url": "redis://localhost:6379/0",
                "use_database_config": True,
                "postgres_dsn": "postgresql://localhost/test",
                "execute_sends": False,
            }
        )

        class _StubStore:
            def get_runtime_snapshot(self):
                alert = Alert.model_validate(
                    {
                        "alert_id": "a-1",
                        "rule_id": "r-1",
                        "rule_version": 1,
                        "user_id": "u-1",
                        "alert_type": "volume_spike_5m",
                        "filters_json": {},
                    }
                )
                return [alert], []

        with patch(
            "alarm_system.service_runtime.build_cached_alert_store",
            return_value=_StubStore(),
        ):
            alerts, bindings = _load_runtime_alert_config(
                config,
                redis_client=object(),
            )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(bindings, [])
