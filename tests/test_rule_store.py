from __future__ import annotations

import unittest
from unittest.mock import patch

from alarm_system.rule_store import (
    PostgresRuleStore,
    RuleSnapshot,
    RuleStoreBackendError,
    RuleStoreContractError,
)


class _MockCursor:
    def __init__(self, *, results: dict[str, object], execute_error: Exception | None = None) -> None:
        self._results = results
        self._execute_error = execute_error
        self._last_query = ""

    def __enter__(self) -> "_MockCursor":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, query: str, params: object = None) -> None:  # noqa: ARG002
        if self._execute_error is not None:
            raise self._execute_error
        self._last_query = query

    def fetchone(self):
        key = self._last_query.strip()
        value = self._results.get(key)
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def fetchall(self):
        key = self._last_query.strip()
        value = self._results.get(key, [])
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


class _MockConn:
    def __init__(self, cursor: _MockCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_MockConn":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def cursor(self) -> _MockCursor:
        return self._cursor


class RuleStoreTests(unittest.TestCase):
    def test_get_active_snapshot_returns_empty_when_no_active_set(self) -> None:
        query = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        cur = _MockCursor(results={query: []})
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            snapshot = store.get_active_snapshot()
        self.assertEqual(snapshot, RuleSnapshot(version=0, rules=[]))

    def test_get_active_snapshot_materializes_rule_tree(self) -> None:
        q_rule_set = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        q_rules = (
            "SELECT "
            "r.rule_pk, r.rule_id, r.version, r.tenant_id, r.name, "
            "r.rule_type, r.object_type, r.severity, r.cooldown_seconds, r.deferred_watch_json "
            "FROM rules r "
            "WHERE r.rule_set_id = %s AND r.enabled = true "
            "ORDER BY r.rule_id ASC, r.version ASC"
        )
        q_groups = (
            "SELECT g.group_id, g.rule_pk, g.parent_group_id, g.bool_op, g.position "
            "FROM rule_groups g "
            "WHERE g.rule_pk = ANY(%s) "
            "ORDER BY g.rule_pk ASC, g.parent_group_id ASC NULLS FIRST, g.position ASC"
        )
        q_predicates = (
            "SELECT p.group_id, p.position, p.field_path, p.comparator, p.operand_json, "
            "p.window_size_seconds, p.window_slide_seconds, p.market_scope "
            "FROM rule_predicates p "
            "JOIN rule_groups g ON g.group_id = p.group_id "
            "WHERE g.rule_pk = ANY(%s) "
            "ORDER BY p.group_id ASC, p.position ASC"
        )
        q_tags = (
            "SELECT rt.rule_pk, t.normalized_label, rt.required "
            "FROM rule_tags rt "
            "JOIN tags t ON t.tag_id = rt.tag_id "
            "WHERE rt.rule_pk = ANY(%s)"
        )
        q_index = (
            "SELECT i.rule_pk, i.object_type, i.field_path "
            "FROM rule_object_field_index i "
            "WHERE i.rule_pk = ANY(%s)"
        )
        cur = _MockCursor(
            results={
                q_rule_set: [(7, 3)],
                q_rules: [
                    (
                        42,
                        "r-price",
                        2,
                        "tenant-a",
                        "Price jump",
                        "volume_spike_5m",
                        "trade",
                        "warning",
                        60,
                        {},
                    )
                ],
                q_groups: [(1001, 42, None, "AND", 0)],
                q_predicates: [
                    (1001, 0, "price_return_1m_pct", "greater_or_equal", 1.5, 60, 10, "single_market")
                ],
                q_tags: [(42, "politics", True)],
                q_index: [(42, "trade", "price_return_1m_pct")],
            }
        )
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            snapshot = store.get_active_snapshot()

        self.assertEqual(snapshot.version, 3)
        self.assertEqual(len(snapshot.rules), 1)
        rule = snapshot.rules[0]
        self.assertEqual(rule.rule_id, "r-price")
        self.assertEqual(rule.version, 2)
        self.assertEqual(rule.filters.category_tags, ["politics"])
        self.assertEqual(rule.field_paths, ["price_return_1m_pct"])

    def test_get_active_version_returns_none_when_empty(self) -> None:
        query = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        cur = _MockCursor(results={query: []})
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            self.assertIsNone(store.get_active_version())

    def test_get_active_version_rejects_multiple_active_sets(self) -> None:
        query = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        cur = _MockCursor(results={query: [(10, 3), (9, 2)]})
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            with self.assertRaises(RuleStoreContractError):
                store.get_active_version()

    def test_get_active_snapshot_preserves_contract_error(self) -> None:
        query = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        cur = _MockCursor(results={query: [(10, 3), (9, 2)]})
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            with self.assertRaises(RuleStoreContractError):
                store.get_active_snapshot()

    def test_backend_error_wraps_missing_schema(self) -> None:
        cur = _MockCursor(
            results={},
            execute_error=RuntimeError('relation "rule_sets" does not exist'),
        )
        store = PostgresRuleStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_MockConn(cur)):
            with self.assertRaises(RuleStoreBackendError) as ctx:
                store.get_active_snapshot()
        self.assertIn("Apply SQL migrations", str(ctx.exception))
