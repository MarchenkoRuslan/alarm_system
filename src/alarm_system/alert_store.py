from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar, Protocol

from alarm_system.entities import Alert, ChannelBinding, DeliveryChannel


class RedisLike(Protocol):
    def get(self, key: str) -> str | bytes | None:
        ...

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        ...

    def delete(self, key: str) -> int:
        ...


class AlertStore(Protocol):
    def list_alerts(
        self,
        *,
        user_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[Alert]:
        ...

    def get_alert(self, alert_id: str) -> Alert | None:
        ...

    def upsert_alert(
        self,
        alert: Alert,
        *,
        expected_version: int | None = None,
    ) -> Alert:
        ...

    def delete_alert(self, alert_id: str) -> bool:
        ...

    def list_bindings(
        self,
        *,
        user_id: str | None = None,
        channel: DeliveryChannel | None = None,
    ) -> list[ChannelBinding]:
        ...

    def get_binding(self, binding_id: str) -> ChannelBinding | None:
        ...

    def upsert_binding(self, binding: ChannelBinding) -> ChannelBinding:
        ...

    def delete_binding(self, binding_id: str) -> bool:
        ...


class AlertStoreConflictError(RuntimeError):
    """Raised when optimistic version check fails."""


class AlertStoreBackendError(RuntimeError):
    """Raised when store backend is unavailable or misconfigured."""


class AlertStoreContractError(ValueError):
    """Raised when API/store contract is used incorrectly."""


ModelT = TypeVar("ModelT", Alert, ChannelBinding)


@dataclass
class InMemoryAlertStore(AlertStore):
    _alerts: dict[str, Alert]
    _bindings: dict[str, ChannelBinding]

    def __init__(self) -> None:
        self._alerts = {}
        self._bindings = {}

    def list_alerts(
        self,
        *,
        user_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[Alert]:
        result: list[Alert] = []
        for alert in self._alerts.values():
            if user_id is not None and alert.user_id != user_id:
                continue
            if not include_disabled and not alert.enabled:
                continue
            result.append(alert)
        return sorted(result, key=lambda item: item.alert_id)

    def get_alert(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    def upsert_alert(
        self,
        alert: Alert,
        *,
        expected_version: int | None = None,
    ) -> Alert:
        if expected_version is None:
            raise AlertStoreContractError(
                "expected_version is required for alert write operations"
            )
        if expected_version < 0:
            raise AlertStoreContractError(
                "expected_version must be >= 0"
            )
        existing = self._alerts.get(alert.alert_id)
        if existing is None and expected_version != 0:
            raise AlertStoreConflictError(
                f"alert {alert.alert_id} does not exist"
            )
        if existing is not None and expected_version == 0:
            raise AlertStoreConflictError(
                f"alert {alert.alert_id} already exists"
            )
        if existing is not None and existing.version != expected_version:
            raise AlertStoreConflictError(
                f"alert {alert.alert_id} version conflict: "
                f"expected={expected_version} actual={existing.version}"
            )
        next_version = 1 if existing is None else existing.version + 1
        saved = alert.model_copy(update={"version": next_version})
        self._alerts[alert.alert_id] = saved
        return saved

    def delete_alert(self, alert_id: str) -> bool:
        return self._alerts.pop(alert_id, None) is not None

    def list_bindings(
        self,
        *,
        user_id: str | None = None,
        channel: DeliveryChannel | None = None,
    ) -> list[ChannelBinding]:
        result: list[ChannelBinding] = []
        for binding in self._bindings.values():
            if user_id is not None and binding.user_id != user_id:
                continue
            if channel is not None and binding.channel is not channel:
                continue
            result.append(binding)
        return sorted(result, key=lambda item: item.binding_id)

    def get_binding(self, binding_id: str) -> ChannelBinding | None:
        return self._bindings.get(binding_id)

    def upsert_binding(self, binding: ChannelBinding) -> ChannelBinding:
        self._bindings[binding.binding_id] = binding
        return binding

    def delete_binding(self, binding_id: str) -> bool:
        return self._bindings.pop(binding_id, None) is not None


class PostgresAlertStore(AlertStore):
    """Postgres-backed source of truth for alert and binding configuration."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'psycopg' package is required for Postgres alert store."
            ) from exc
        return psycopg.connect(self._dsn)

    def list_alerts(
        self,
        *,
        user_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[Alert]:
        query = "SELECT payload_json FROM alert_configs"
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = %s")
            params.append(user_id)
        if not include_disabled:
            clauses.append("enabled = true")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY alert_id ASC"
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="list alerts",
            ) from exc
        return [_model_from_db_payload(Alert, row[0]) for row in rows]

    def get_alert(self, alert_id: str) -> Alert | None:
        query = "SELECT payload_json FROM alert_configs WHERE alert_id = %s"
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query, (alert_id,))
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="get alert",
            ) from exc
        if row is None:
            return None
        return _model_from_db_payload(Alert, row[0])

    def upsert_alert(
        self,
        alert: Alert,
        *,
        expected_version: int | None = None,
    ) -> Alert:
        if expected_version is None:
            raise AlertStoreContractError(
                "expected_version is required for alert write operations"
            )
        if expected_version < 0:
            raise AlertStoreContractError(
                "expected_version must be >= 0"
            )
        now = datetime.now(timezone.utc)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                if expected_version == 0:
                    saved = alert.model_copy(update={"version": 1})
                    cur.execute(
                        "INSERT INTO alert_configs ("
                        "alert_id, user_id, enabled, version, payload_json, updated_at"
                        ") VALUES (%s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (alert_id) DO NOTHING",
                        (
                            saved.alert_id,
                            saved.user_id,
                            saved.enabled,
                            saved.version,
                            _pg_jsonb(saved.model_dump(mode="json")),
                            now,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise self._version_conflict(cur, alert.alert_id, 0)
                    conn.commit()
                    return saved

                cur.execute(
                    "UPDATE alert_configs SET "
                    "user_id = %s, "
                    "enabled = %s, "
                    "version = %s, "
                    "payload_json = %s, "
                    "updated_at = %s "
                    "WHERE alert_id = %s AND version = %s",
                    (
                        alert.user_id,
                        alert.enabled,
                        expected_version + 1,
                        _pg_jsonb(
                            alert.model_copy(
                                update={"version": expected_version + 1}
                            ).model_dump(mode="json")
                        ),
                        now,
                        alert.alert_id,
                        expected_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise self._version_conflict(
                        cur,
                        alert.alert_id,
                        expected_version,
                    )
                conn.commit()
                return alert.model_copy(update={"version": expected_version + 1})
        except AlertStoreConflictError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="upsert alert",
            ) from exc

    def delete_alert(self, alert_id: str) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM alert_configs WHERE alert_id = %s",
                    (alert_id,),
                )
                deleted = cur.rowcount > 0
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="delete alert",
            ) from exc
        return deleted

    def list_bindings(
        self,
        *,
        user_id: str | None = None,
        channel: DeliveryChannel | None = None,
    ) -> list[ChannelBinding]:
        query = "SELECT payload_json FROM channel_bindings"
        clauses: list[str] = []
        params: list[object] = []
        if user_id is not None:
            clauses.append("user_id = %s")
            params.append(user_id)
        if channel is not None:
            clauses.append("channel = %s")
            params.append(channel.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY binding_id ASC"
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="list channel bindings",
            ) from exc
        return [_model_from_db_payload(ChannelBinding, row[0]) for row in rows]

    def get_binding(self, binding_id: str) -> ChannelBinding | None:
        query = (
            "SELECT payload_json FROM channel_bindings WHERE binding_id = %s"
        )
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query, (binding_id,))
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="get channel binding",
            ) from exc
        if row is None:
            return None
        return _model_from_db_payload(ChannelBinding, row[0])

    def upsert_binding(self, binding: ChannelBinding) -> ChannelBinding:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO channel_bindings ("
                    "binding_id, user_id, channel, destination, is_verified, "
                    "payload_json, updated_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (binding_id) DO UPDATE SET "
                    "user_id = EXCLUDED.user_id, "
                    "channel = EXCLUDED.channel, "
                    "destination = EXCLUDED.destination, "
                    "is_verified = EXCLUDED.is_verified, "
                    "payload_json = EXCLUDED.payload_json, "
                    "updated_at = EXCLUDED.updated_at",
                    (
                        binding.binding_id,
                        binding.user_id,
                        binding.channel.value,
                        binding.destination,
                        binding.is_verified,
                        _pg_jsonb(binding.model_dump(mode="json")),
                        datetime.now(timezone.utc),
                    ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="upsert channel binding",
            ) from exc
        return binding

    def delete_binding(self, binding_id: str) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM channel_bindings WHERE binding_id = %s",
                    (binding_id,),
                )
                deleted = cur.rowcount > 0
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            raise _to_backend_error(
                exc,
                operation="delete channel binding",
            ) from exc
        return deleted

    def _version_conflict(
        self,
        cur: Any,
        alert_id: str,
        expected_version: int,
    ) -> AlertStoreConflictError:
        cur.execute(
            "SELECT version FROM alert_configs WHERE alert_id = %s",
            (alert_id,),
        )
        row = cur.fetchone()
        actual_version = int(row[0]) if row is not None else 0
        return AlertStoreConflictError(
            f"alert {alert_id} version conflict: "
            f"expected={expected_version} actual={actual_version}"
        )


@dataclass
class RedisAlertCache:
    redis_client: RedisLike
    prefix: str = "alarm:config"
    ttl_seconds: int = 30

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def load_runtime_snapshot(
        self,
    ) -> tuple[list[Alert], list[ChannelBinding]] | None:
        raw_alerts = self.redis_client.get(self._key("runtime:alerts"))
        raw_bindings = self.redis_client.get(self._key("runtime:bindings"))
        if raw_alerts is None or raw_bindings is None:
            return None
        alerts_json = _decode_redis(raw_alerts)
        bindings_json = _decode_redis(raw_bindings)
        try:
            parsed_alerts = json.loads(alerts_json)
            parsed_bindings = json.loads(bindings_json)
        except json.JSONDecodeError:
            return None
        alerts = [Alert.model_validate(item) for item in parsed_alerts]
        bindings = [
            ChannelBinding.model_validate(item) for item in parsed_bindings
        ]
        return alerts, bindings

    def store_runtime_snapshot(
        self,
        *,
        alerts: list[Alert],
        bindings: list[ChannelBinding],
    ) -> None:
        self.redis_client.set(
            self._key("runtime:alerts"),
            json.dumps([item.model_dump(mode="json") for item in alerts]),
            ex=self.ttl_seconds,
        )
        self.redis_client.set(
            self._key("runtime:bindings"),
            json.dumps([item.model_dump(mode="json") for item in bindings]),
            ex=self.ttl_seconds,
        )

    def invalidate_runtime_snapshot(self) -> None:
        self.redis_client.delete(self._key("runtime:alerts"))
        self.redis_client.delete(self._key("runtime:bindings"))


@dataclass
class CachedAlertStore(AlertStore):
    primary: AlertStore
    cache: RedisAlertCache
    cache_refresh_seconds: int = 30

    def get_runtime_snapshot(
        self,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[Alert], list[ChannelBinding]]:
        if not force_refresh:
            cached = self.cache.load_runtime_snapshot()
            if cached is not None:
                return cached
        alerts = self.primary.list_alerts(include_disabled=False)
        bindings = self.primary.list_bindings()
        self.cache.store_runtime_snapshot(alerts=alerts, bindings=bindings)
        return alerts, bindings

    def list_alerts(
        self,
        *,
        user_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[Alert]:
        return self.primary.list_alerts(
            user_id=user_id,
            include_disabled=include_disabled,
        )

    def get_alert(self, alert_id: str) -> Alert | None:
        return self.primary.get_alert(alert_id)

    def upsert_alert(
        self,
        alert: Alert,
        *,
        expected_version: int | None = None,
    ) -> Alert:
        saved = self.primary.upsert_alert(
            alert,
            expected_version=expected_version,
        )
        self.cache.invalidate_runtime_snapshot()
        return saved

    def delete_alert(self, alert_id: str) -> bool:
        deleted = self.primary.delete_alert(alert_id)
        if deleted:
            self.cache.invalidate_runtime_snapshot()
        return deleted

    def list_bindings(
        self,
        *,
        user_id: str | None = None,
        channel: DeliveryChannel | None = None,
    ) -> list[ChannelBinding]:
        return self.primary.list_bindings(user_id=user_id, channel=channel)

    def get_binding(self, binding_id: str) -> ChannelBinding | None:
        return self.primary.get_binding(binding_id)

    def upsert_binding(self, binding: ChannelBinding) -> ChannelBinding:
        saved = self.primary.upsert_binding(binding)
        self.cache.invalidate_runtime_snapshot()
        return saved

    def delete_binding(self, binding_id: str) -> bool:
        deleted = self.primary.delete_binding(binding_id)
        if deleted:
            self.cache.invalidate_runtime_snapshot()
        return deleted


def build_cached_alert_store(
    *,
    postgres_dsn: str,
    redis_client: RedisLike,
    cache_prefix: str = "alarm:config",
    cache_ttl_seconds: int = 30,
) -> CachedAlertStore:
    return CachedAlertStore(
        primary=PostgresAlertStore(postgres_dsn),
        cache=RedisAlertCache(
            redis_client=redis_client,
            prefix=cache_prefix,
            ttl_seconds=cache_ttl_seconds,
        ),
        cache_refresh_seconds=cache_ttl_seconds,
    )


def _decode_redis(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _pg_jsonb(payload: object) -> object:
    """Adapt payload for Postgres JSONB writes with psycopg3."""
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        # Keeps tests and non-Postgres contexts decoupled from psycopg extras.
        return json.dumps(payload)
    return Jsonb(payload)


def _model_from_db_payload(model: type[ModelT], payload: Any) -> ModelT:
    if isinstance(payload, (dict, list)):
        return model.model_validate(payload)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return model.model_validate_json(payload)
    raise AlertStoreBackendError(
        f"Unsupported payload type from database: {type(payload).__name__}"
    )


def _to_backend_error(exc: Exception, *, operation: str) -> AlertStoreBackendError:
    message = str(exc).lower()
    if "does not exist" in message and "relation" in message:
        return AlertStoreBackendError(
            "Postgres schema is missing required tables. "
            "Apply SQL migrations before running API/runtime."
        )
    return AlertStoreBackendError(
        f"Postgres alert store failed to {operation}: {exc}"
    )
