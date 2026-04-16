from __future__ import annotations

import unittest
from unittest.mock import patch

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreContractError,
    AlertStoreConflictError,
    CachedAlertStore,
    InMemoryAlertStore,
    PostgresAlertStore,
    RedisAlertCache,
    _model_from_db_payload,
)
from alarm_system.entities import Alert, ChannelBinding, DeliveryChannel


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self._store[key] = value
        return True

    def delete(self, key: str) -> int:
        if key not in self._store:
            return 0
        del self._store[key]
        return 1


def _alert(alert_id: str) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": alert_id,
            "rule_id": "r-1",
            "rule_version": 1,
            "user_id": "u-1",
            "alert_type": "volume_spike_5m",
            "filters_json": {},
        }
    )


class AlertStoreTests(unittest.TestCase):
    def test_in_memory_store_enforces_optimistic_version(self) -> None:
        store = InMemoryAlertStore()
        first = store.upsert_alert(_alert("a-1"), expected_version=0)
        self.assertEqual(first.version, 1)
        with self.assertRaises(AlertStoreConflictError):
            store.upsert_alert(_alert("a-1"), expected_version=0)
        second = store.upsert_alert(_alert("a-1"), expected_version=1)
        self.assertEqual(second.version, 2)
        with self.assertRaises(AlertStoreContractError):
            store.upsert_alert(_alert("a-1"))

    def test_cached_store_reads_from_cache_after_warmup(self) -> None:
        primary = InMemoryAlertStore()
        primary.upsert_alert(_alert("a-1"), expected_version=0)
        primary.upsert_binding(
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "123",
                    "is_verified": True,
                }
            )
        )
        cache = RedisAlertCache(redis_client=_FakeRedis(), ttl_seconds=60)
        store = CachedAlertStore(primary=primary, cache=cache)
        first_alerts, first_bindings = store.get_runtime_snapshot()
        self.assertEqual(len(first_alerts), 1)
        self.assertEqual(len(first_bindings), 1)

        primary.delete_alert("a-1")
        second_alerts, _ = store.get_runtime_snapshot()
        self.assertEqual(len(second_alerts), 1)

        refreshed_alerts, _ = store.get_runtime_snapshot(force_refresh=True)
        self.assertEqual(len(refreshed_alerts), 0)

    def test_model_from_db_payload_supports_dict_and_json_string(self) -> None:
        from_dict = _model_from_db_payload(
            Alert,
            {
                "alert_id": "a-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": "volume_spike_5m",
                "filters_json": {},
            },
        )
        from_str = _model_from_db_payload(
            Alert,
            '{"alert_id":"a-2","rule_id":"r-1","rule_version":1,'
            '"user_id":"u-1","alert_type":"volume_spike_5m","filters_json":{}}',
        )
        self.assertEqual(from_dict.alert_id, "a-1")
        self.assertEqual(from_str.alert_id, "a-2")

    def test_postgres_store_wraps_missing_schema_error(self) -> None:
        class _FailingCursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                raise RuntimeError('relation "alert_configs" does not exist')

            def fetchall(self):
                return []

        class _FailingConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return _FailingCursor()

        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_FailingConn()):
            with self.assertRaises(AlertStoreBackendError) as ctx:
                store.list_alerts()
        self.assertIn("Apply SQL migrations", str(ctx.exception))

    def test_postgres_store_returns_conflict_for_atomic_update(self) -> None:
        class _Cursor:
            def __init__(self):
                self.rowcount = 0
                self._select_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                if query.startswith("UPDATE alert_configs"):
                    self.rowcount = 0
                if query.startswith("SELECT version FROM alert_configs"):
                    self._select_calls += 1

            def fetchone(self):
                return (2,)

        class _Conn:
            def __init__(self):
                self._cursor = _Cursor()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return self._cursor

            def commit(self):
                return None

        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=_Conn()):
            with self.assertRaises(AlertStoreConflictError) as ctx:
                store.upsert_alert(_alert("a-1"), expected_version=1)
        self.assertIn("expected=1 actual=2", str(ctx.exception))

    def test_postgres_store_requires_expected_version(self) -> None:
        store = PostgresAlertStore("postgresql://localhost/test")
        with self.assertRaises(AlertStoreContractError):
            store.upsert_alert(_alert("a-1"))
