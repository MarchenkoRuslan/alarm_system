from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarm_system.api.rule_catalog import (
    invalidate_rule_catalog_cache,
    load_rules_cached,
)
from alarm_system.rule_store import RuleSnapshot
from alarm_system.rules_dsl import AlertRuleV1


def _rule_payload(rule_id: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "tenant_id": "tenant-a",
        "name": rule_id,
        "rule_type": "volume_spike_5m",
        "version": 1,
        "expression": {
            "signal": "price_return_1m_pct",
            "op": "gte",
            "threshold": 1.0,
            "window": {"size_seconds": 60, "slide_seconds": 10},
        },
    }


class RuleCatalogCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        invalidate_rule_catalog_cache()

    def test_db_mode_does_not_reuse_file_cache_when_active_snapshot_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            rules_path = Path(tmp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps([_rule_payload("file-rule")]),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "ALARM_USE_DATABASE_RULES": "false",
                    "ALARM_RULES_PATH": str(rules_path),
                },
                clear=False,
            ):
                file_rules = load_rules_cached(force_reload=True)
                self.assertEqual(file_rules[0].rule_id, "file-rule")

            class _EmptyDbStore:
                def get_active_version(self) -> None:
                    return None

                def get_active_snapshot(self) -> RuleSnapshot:
                    return RuleSnapshot(version=0, rules=[])

            with patch.dict(
                "os.environ",
                {
                    "ALARM_USE_DATABASE_RULES": "true",
                    "ALARM_POSTGRES_DSN": "postgresql://localhost/test",
                    "ALARM_RULES_PATH": str(rules_path),
                },
                clear=False,
            ), patch(
                "alarm_system.api.rule_catalog.PostgresRuleStore",
                return_value=_EmptyDbStore(),
            ):
                with self.assertRaises(ValueError) as ctx:
                    load_rules_cached(force_reload=False)
        self.assertIn("Strict SSOT mode", str(ctx.exception))

    def test_switch_from_db_to_file_mode_drops_db_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            rules_path = Path(tmp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps([_rule_payload("file-rule")]),
                encoding="utf-8",
            )

            db_rule = AlertRuleV1.model_validate(_rule_payload("db-rule"))

            class _DbStore:
                def get_active_version(self) -> int:
                    return 7

                def get_active_snapshot(self) -> RuleSnapshot:
                    return RuleSnapshot(version=7, rules=[db_rule])

            with patch.dict(
                "os.environ",
                {
                    "ALARM_USE_DATABASE_RULES": "true",
                    "ALARM_POSTGRES_DSN": "postgresql://localhost/test",
                },
                clear=False,
            ), patch(
                "alarm_system.api.rule_catalog.PostgresRuleStore",
                return_value=_DbStore(),
            ):
                rules = load_rules_cached(force_reload=True)
                self.assertEqual(rules[0].rule_id, "db-rule")

            with patch.dict(
                "os.environ",
                {
                    "ALARM_USE_DATABASE_RULES": "false",
                    "ALARM_RULES_PATH": str(rules_path),
                },
                clear=False,
            ):
                rules = load_rules_cached(force_reload=False)
        self.assertEqual(rules[0].rule_id, "file-rule")
