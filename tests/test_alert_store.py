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
    _to_backend_error,
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


class _MockCursor:
    def __init__(
        self,
        rowcount: int = 0,
        fetchone_result: object = None,
        execute_error: Exception | None = None,
    ) -> None:
        self.rowcount = rowcount
        self._fetchone_result = fetchone_result
        self._execute_error = execute_error

    def __enter__(self) -> "_MockCursor":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, query: str, params: object = None) -> None:
        if self._execute_error:
            raise self._execute_error

    def fetchall(self) -> list:
        return []

    def fetchone(self) -> object:
        return self._fetchone_result


class _MockConn:
    def __init__(self, cursor: _MockCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def __enter__(self) -> "_MockConn":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def cursor(self) -> _MockCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def _make_pg_mock(
    rowcount: int = 0,
    fetchone: object = None,
    execute_error: Exception | None = None,
) -> tuple[_MockConn, _MockCursor]:
    cur = _MockCursor(rowcount=rowcount, fetchone_result=fetchone, execute_error=execute_error)
    return _MockConn(cursor=cur), cur


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

    def test_model_from_db_payload_supports_bytes_and_rejects_unknown_type(self) -> None:
        from_bytes = _model_from_db_payload(
            Alert,
            (
                b'{"alert_id":"a-3","rule_id":"r-1","rule_version":1,'
                b'"user_id":"u-1","alert_type":"volume_spike_5m","filters_json":{}}'
            ),
        )
        self.assertEqual(from_bytes.alert_id, "a-3")

        with self.assertRaises(AlertStoreBackendError):
            _model_from_db_payload(Alert, 42)

    def test_to_backend_error_non_relation_path_contains_operation(self) -> None:
        error = _to_backend_error(RuntimeError("network timeout"), operation="list alerts")
        self.assertIn("failed to list alerts", str(error))

    def test_postgres_store_wraps_missing_schema_error(self) -> None:
        conn, _ = _make_pg_mock(
            execute_error=RuntimeError('relation "alert_configs" does not exist')
        )
        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=conn):
            with self.assertRaises(AlertStoreBackendError) as ctx:
                store.list_alerts()
        self.assertIn("Apply SQL migrations", str(ctx.exception))

    def test_postgres_store_returns_conflict_for_atomic_update(self) -> None:
        conn, _ = _make_pg_mock(rowcount=0, fetchone=(2,))
        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=conn):
            with self.assertRaises(AlertStoreConflictError) as ctx:
                store.upsert_alert(_alert("a-1"), expected_version=1)
        self.assertIn("expected=1 actual=2", str(ctx.exception))

    def test_postgres_store_requires_expected_version(self) -> None:
        store = PostgresAlertStore("postgresql://localhost/test")
        with self.assertRaises(AlertStoreContractError):
            store.upsert_alert(_alert("a-1"))

    def test_postgres_store_maps_delete_and_list_bindings_backend_errors(self) -> None:
        conn, _ = _make_pg_mock(execute_error=RuntimeError("socket closed"))
        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=conn):
            with self.assertRaises(AlertStoreBackendError):
                store.delete_alert("a-1")
            with self.assertRaises(AlertStoreBackendError):
                store.list_bindings()

    def test_postgres_store_create_success_with_expected_version_zero(self) -> None:
        conn, _ = _make_pg_mock(rowcount=1)
        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=conn):
            saved = store.upsert_alert(_alert("a-create"), expected_version=0)
        self.assertEqual(saved.version, 1)
        self.assertTrue(conn.committed)

    def test_postgres_store_update_success_with_expected_version(self) -> None:
        conn, _ = _make_pg_mock(rowcount=1)
        store = PostgresAlertStore("postgresql://localhost/test")
        with patch.object(store, "_connect", return_value=conn):
            saved = store.upsert_alert(_alert("a-update"), expected_version=1)
        self.assertEqual(saved.version, 2)
        self.assertTrue(conn.committed)
