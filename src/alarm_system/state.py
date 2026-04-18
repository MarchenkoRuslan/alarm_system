from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from alarm_system.dedup import DedupInput, cooldown_key, dedup_key
from alarm_system.entities import DeliveryAttempt, DeliveryChannel
from alarm_system.rules_dsl import TriggerReason


class RedisLike(Protocol):
    """Small subset used by runtime state stores."""

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

    def rpush(self, key: str, *values: str) -> int:
        ...

    def lrange(self, key: str, start: int, end: int) -> list[str | bytes]:
        ...


@dataclass(frozen=True)
class TriggerAuditRecord:
    trigger_id: str
    trigger_key: str
    alert_id: str
    rule_id: str
    rule_version: int
    tenant_id: str
    scope_id: str
    reason: TriggerReason
    event_ts: datetime
    evaluated_at: datetime
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_reason_json(self) -> str:
        return self.reason.model_dump_json()


class TriggerAuditStore(Protocol):
    def save_once(self, record: TriggerAuditRecord) -> bool:
        ...

    def all(self) -> list[TriggerAuditRecord]:
        ...


class InMemoryTriggerAuditStore:
    def __init__(self) -> None:
        self._records: dict[str, TriggerAuditRecord] = {}

    def save_once(self, record: TriggerAuditRecord) -> bool:
        if record.trigger_key in self._records:
            return False
        self._records[record.trigger_key] = record
        return True

    def all(self) -> list[TriggerAuditRecord]:
        return list(self._records.values())


class RedisTriggerAuditStore:
    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:trigger_audit",
        index_key: str = "alarm:trigger_audit:index",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._index_key = index_key

    def save_once(self, record: TriggerAuditRecord) -> bool:
        key = f"{self._prefix}:{record.trigger_key}"
        payload = json.dumps(
            {
                "trigger_id": record.trigger_id,
                "trigger_key": record.trigger_key,
                "alert_id": record.alert_id,
                "rule_id": record.rule_id,
                "rule_version": record.rule_version,
                "tenant_id": record.tenant_id,
                "scope_id": record.scope_id,
                "reason_json": record.to_reason_json(),
                "event_ts": _ensure_utc(record.event_ts).isoformat(),
                "evaluated_at": _ensure_utc(record.evaluated_at).isoformat(),
                "created_at": _ensure_utc(record.created_at).isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        created = self._redis.set(key, payload, nx=True)
        if not created:
            return False
        self._redis.rpush(self._index_key, record.trigger_key)
        return True

    def all(self) -> list[TriggerAuditRecord]:
        result: list[TriggerAuditRecord] = []
        for raw_key in self._redis.lrange(self._index_key, 0, -1):
            trigger_key = (
                raw_key.decode("utf-8")
                if isinstance(raw_key, bytes)
                else str(raw_key)
            )
            value = self._redis.get(f"{self._prefix}:{trigger_key}")
            if value is None:
                continue
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            reason_json = parsed.get("reason_json")
            if not isinstance(reason_json, str):
                continue
            try:
                result.append(
                    TriggerAuditRecord(
                        trigger_id=str(parsed["trigger_id"]),
                        trigger_key=str(parsed["trigger_key"]),
                        alert_id=str(parsed["alert_id"]),
                        rule_id=str(parsed["rule_id"]),
                        rule_version=int(parsed["rule_version"]),
                        tenant_id=str(parsed["tenant_id"]),
                        scope_id=str(parsed["scope_id"]),
                        reason=TriggerReason.model_validate_json(reason_json),
                        event_ts=datetime.fromisoformat(
                            str(parsed["event_ts"])
                        ),
                        evaluated_at=datetime.fromisoformat(
                            str(parsed["evaluated_at"])
                        ),
                        created_at=datetime.fromisoformat(
                            str(parsed["created_at"])
                        ),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return result


class DeliveryIdempotencyStore(Protocol):
    def reserve(self, key: str, ttl_seconds: int) -> bool:
        ...


class InMemoryDeliveryIdempotencyStore:
    def __init__(self) -> None:
        self._active_until: dict[str, datetime] = {}

    def reserve(self, key: str, ttl_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        active_until = self._active_until.get(key)
        if active_until is not None and now < active_until:
            return False
        self._active_until[key] = now + timedelta(seconds=ttl_seconds)
        return True


class RedisDeliveryIdempotencyStore:
    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:delivery:idempotency",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def reserve(self, key: str, ttl_seconds: int) -> bool:
        redis_key = f"{self._prefix}:{key}"
        created = self._redis.set(redis_key, "1", ex=ttl_seconds, nx=True)
        return bool(created)


class TriggerDedupStore(Protocol):
    def reserve(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        event_time: datetime,
        bucket_seconds: int,
        ttl_seconds: int,
    ) -> tuple[bool, str]:
        ...


class InMemoryTriggerDedupStore:
    def __init__(self) -> None:
        self._active_until: dict[str, datetime] = {}

    def reserve(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        event_time: datetime,
        bucket_seconds: int,
        ttl_seconds: int,
    ) -> tuple[bool, str]:
        now = _ensure_utc(event_time)
        key = dedup_key(
            DedupInput(
                tenant_id=tenant_id,
                rule_id=rule_id,
                rule_version=rule_version,
                scope_id=scope_id,
                bucket_seconds=bucket_seconds,
                event_time=now,
            )
        )
        expires = self._active_until.get(key)
        if expires is not None and now < expires:
            return False, key
        self._active_until[key] = now + timedelta(seconds=ttl_seconds)
        return True, key


class RedisTriggerDedupStore:
    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:dedup",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def reserve(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        event_time: datetime,
        bucket_seconds: int,
        ttl_seconds: int,
    ) -> tuple[bool, str]:
        raw_key = dedup_key(
            DedupInput(
                tenant_id=tenant_id,
                rule_id=rule_id,
                rule_version=rule_version,
                scope_id=scope_id,
                bucket_seconds=bucket_seconds,
                event_time=_ensure_utc(event_time),
            )
        )
        key = f"{self._prefix}:{raw_key}"
        created = self._redis.set(key, "1", ex=ttl_seconds, nx=True)
        return bool(created), raw_key


class CooldownStore(Protocol):
    def allow(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        channel: DeliveryChannel,
        triggered_at: datetime,
        cooldown_seconds: int,
    ) -> bool:
        ...


class InMemoryCooldownStore:
    def __init__(self) -> None:
        self._active_until: dict[str, datetime] = {}

    def allow(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        channel: DeliveryChannel,
        triggered_at: datetime,
        cooldown_seconds: int,
    ) -> bool:
        key = cooldown_key(
            tenant_id=tenant_id,
            rule_id=rule_id,
            rule_version=rule_version,
            scope_id=scope_id,
            channel=channel.value,
        )
        at = _ensure_utc(triggered_at)
        expires = self._active_until.get(key)
        if expires is not None and at < expires:
            return False
        self._active_until[key] = at + timedelta(seconds=cooldown_seconds)
        return True


class RedisCooldownStore:
    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:cooldown",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def allow(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        channel: DeliveryChannel,
        triggered_at: datetime,
        cooldown_seconds: int,
    ) -> bool:
        if cooldown_seconds <= 0:
            return True
        raw_key = cooldown_key(
            tenant_id=tenant_id,
            rule_id=rule_id,
            rule_version=rule_version,
            scope_id=scope_id,
            channel=channel.value,
        )
        key = f"{self._prefix}:{raw_key}"
        created = self._redis.set(key, "1", ex=cooldown_seconds, nx=True)
        return bool(created)


class DeliveryAttemptStore(Protocol):
    """Audit log for delivery attempts.

    ``save_for_user`` is the preferred path for dispatcher code since it
    populates a per-user index used by the ``/history`` Telegram
    command. The ``DeliveryDispatcher`` falls back to ``save`` via
    ``getattr`` when the store does not implement ``save_for_user``,
    which keeps older custom stubs working while new implementations
    are expected to provide both methods.
    """

    def save(self, attempt: DeliveryAttempt) -> None:
        ...

    def save_for_user(self, attempt: DeliveryAttempt, *, user_id: str) -> None:
        ...

    def all(self) -> list[DeliveryAttempt]:
        ...

    def list_by_user(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[DeliveryAttempt]:
        ...


class InMemoryDeliveryAttemptStore:
    DEFAULT_USER_INDEX_MAX_LEN = 500

    def __init__(
        self,
        *,
        user_index_max_len: int = DEFAULT_USER_INDEX_MAX_LEN,
    ) -> None:
        self._attempts: list[DeliveryAttempt] = []
        self._by_user: dict[str, list[DeliveryAttempt]] = {}
        self._user_index_max_len = user_index_max_len

    def save(self, attempt: DeliveryAttempt) -> None:
        self._attempts.append(attempt)

    def save_for_user(self, attempt: DeliveryAttempt, *, user_id: str) -> None:
        self._attempts.append(attempt)
        bucket = self._by_user.setdefault(user_id, [])
        bucket.append(attempt)
        if len(bucket) > self._user_index_max_len:
            # Bound memory symmetrically with RedisDeliveryAttemptStore.
            del bucket[: len(bucket) - self._user_index_max_len]

    def all(self) -> list[DeliveryAttempt]:
        return list(self._attempts)

    def list_by_user(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[DeliveryAttempt]:
        if limit <= 0:
            return []
        attempts = self._by_user.get(user_id, [])
        return list(reversed(attempts[-limit:]))


class RedisDeliveryAttemptStore:
    """Redis-backed delivery attempt log with bounded retention.

    Retention policy (see ``docs/architecture/state-delivery-entry-design.md``):

    - Individual attempt records live under
      ``alarm:delivery_attempt:{id}`` with a 7-day TTL by default so
      operational history never grows unbounded.
    - The global ``alarm:delivery_attempt:index`` list is trimmed to
      ``main_index_max_len`` entries (default 10_000) after every push.
    - Per-user indices ``alarm:delivery_attempt:by_user:{user_id}`` are
      trimmed to ``user_index_max_len`` entries (default 500) and serve
      the ``/history`` command without scanning the global index.
    """

    DEFAULT_ATTEMPT_TTL_SECONDS = 7 * 24 * 3600
    DEFAULT_MAIN_INDEX_MAX_LEN = 10_000
    DEFAULT_USER_INDEX_MAX_LEN = 500

    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:delivery_attempt",
        index_key: str = "alarm:delivery_attempt:index",
        user_index_prefix: str = "alarm:delivery_attempt:by_user",
        user_index_max_len: int = DEFAULT_USER_INDEX_MAX_LEN,
        main_index_max_len: int = DEFAULT_MAIN_INDEX_MAX_LEN,
        attempt_ttl_seconds: int = DEFAULT_ATTEMPT_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._index_key = index_key
        self._user_index_prefix = user_index_prefix
        self._user_index_max_len = user_index_max_len
        self._main_index_max_len = main_index_max_len
        self._attempt_ttl_seconds = attempt_ttl_seconds

    def save(self, attempt: DeliveryAttempt) -> None:
        key = f"{self._prefix}:{attempt.attempt_id}"
        self._redis.set(
            key,
            attempt.model_dump_json(),
            ex=self._attempt_ttl_seconds,
        )
        self._redis.rpush(self._index_key, attempt.attempt_id)
        self._trim_list(self._index_key, self._main_index_max_len)

    def save_for_user(self, attempt: DeliveryAttempt, *, user_id: str) -> None:
        """Persist a per-user index entry alongside the main record.

        The main ``save`` path is unchanged; dispatchers that know the
        alert owner call this variant to enable efficient
        ``list_by_user`` queries without scanning the whole index.
        """

        self.save(attempt)
        user_key = f"{self._user_index_prefix}:{user_id}"
        self._redis.rpush(user_key, attempt.attempt_id)
        self._trim_list(user_key, self._user_index_max_len)

    def _trim_list(self, key: str, max_len: int) -> None:
        """Best-effort ``LTRIM key -max_len -1``.

        Uses ``getattr`` so stores that implement the minimal
        ``RedisLike`` protocol without ``ltrim`` still function; any
        runtime error is swallowed since trimming is an optimisation
        and must not break the hot write path.
        """

        trim = getattr(self._redis, "ltrim", None)
        if trim is None:
            return
        try:
            trim(key, -max_len, -1)
        except Exception:  # noqa: BLE001
            return

    def all(self) -> list[DeliveryAttempt]:
        attempts: list[DeliveryAttempt] = []
        for raw_id in self._redis.lrange(self._index_key, 0, -1):
            attempt_id = (
                raw_id.decode("utf-8")
                if isinstance(raw_id, bytes)
                else str(raw_id)
            )
            raw = self._redis.get(f"{self._prefix}:{attempt_id}")
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                attempts.append(DeliveryAttempt.model_validate_json(raw))
            except ValueError:
                continue
        return attempts

    def list_by_user(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[DeliveryAttempt]:
        if limit <= 0:
            return []
        user_key = f"{self._user_index_prefix}:{user_id}"
        raw_ids = self._redis.lrange(user_key, -limit, -1)
        attempts: list[DeliveryAttempt] = []
        for raw_id in reversed(list(raw_ids)):
            attempt_id = (
                raw_id.decode("utf-8")
                if isinstance(raw_id, bytes)
                else str(raw_id)
            )
            raw = self._redis.get(f"{self._prefix}:{attempt_id}")
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                attempts.append(DeliveryAttempt.model_validate_json(raw))
            except ValueError:
                continue
        return attempts


class MuteStore(Protocol):
    def set_mute(self, *, user_id: str, seconds: int) -> datetime:
        ...

    def get_mute_until(self, user_id: str) -> datetime | None:
        ...

    def clear_mute(self, user_id: str) -> bool:
        ...


class InMemoryMuteStore:
    def __init__(self) -> None:
        self._active_until: dict[str, datetime] = {}

    def set_mute(self, *, user_id: str, seconds: int) -> datetime:
        if seconds <= 0:
            raise ValueError("mute duration must be positive")
        active_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._active_until[user_id] = active_until
        return active_until

    def get_mute_until(self, user_id: str) -> datetime | None:
        active_until = self._active_until.get(user_id)
        if active_until is None:
            return None
        if datetime.now(timezone.utc) >= active_until:
            self._active_until.pop(user_id, None)
            return None
        return active_until

    def clear_mute(self, user_id: str) -> bool:
        return self._active_until.pop(user_id, None) is not None


class RedisMuteStore:
    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:mute",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def _key(self, user_id: str) -> str:
        return f"{self._prefix}:{user_id}"

    def set_mute(self, *, user_id: str, seconds: int) -> datetime:
        if seconds <= 0:
            raise ValueError("mute duration must be positive")
        active_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._redis.set(
            self._key(user_id),
            active_until.isoformat(),
            ex=seconds,
        )
        return active_until

    def get_mute_until(self, user_id: str) -> datetime | None:
        value = self._redis.get(self._key(user_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        parsed = _ensure_utc(parsed)
        if datetime.now(timezone.utc) >= parsed:
            return None
        return parsed

    def clear_mute(self, user_id: str) -> bool:
        removed = self._redis.delete(self._key(user_id))
        try:
            return int(removed) > 0
        except (TypeError, ValueError):
            return bool(removed)


class SessionStore(Protocol):
    """Transient per-user dialog state for the interactive Telegram UI.

    Stores a short-lived JSON payload keyed by ``user_id`` — typically
    the FSM state of a create-alert wizard (``step``, ``draft``,
    ``message_id``, ``rule_type``, ...). One active session per user is
    expected; starting a new wizard overrides any previous draft.
    """

    def save(self, *, user_id: str, payload: dict, ttl_seconds: int) -> None:
        ...

    def load(self, user_id: str) -> dict | None:
        ...

    def clear(self, user_id: str) -> bool:
        ...


class InMemorySessionStore:
    """In-memory ``SessionStore`` for dev/test runs."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[dict, datetime]] = {}

    def save(self, *, user_id: str, payload: dict, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("session ttl must be positive")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._sessions[user_id] = (dict(payload), expires_at)

    def load(self, user_id: str) -> dict | None:
        entry = self._sessions.get(user_id)
        if entry is None:
            return None
        payload, expires_at = entry
        if datetime.now(timezone.utc) >= expires_at:
            self._sessions.pop(user_id, None)
            return None
        return dict(payload)

    def clear(self, user_id: str) -> bool:
        return self._sessions.pop(user_id, None) is not None


class RedisSessionStore:
    """Redis-backed ``SessionStore``.

    Single key per user: ``<prefix>:<user_id>`` with TTL equal to the
    session TTL so stale drafts never leak indefinitely.
    """

    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:session",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def _key(self, user_id: str) -> str:
        return f"{self._prefix}:{user_id}"

    def save(self, *, user_id: str, payload: dict, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("session ttl must be positive")
        self._redis.set(
            self._key(user_id),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            ex=ttl_seconds,
        )

    def load(self, user_id: str) -> dict | None:
        value = self._redis.get(self._key(user_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def clear(self, user_id: str) -> bool:
        removed = self._redis.delete(self._key(user_id))
        try:
            return int(removed) > 0
        except (TypeError, ValueError):
            return bool(removed)


class RedisSuppressionWindowStateStore:
    """
    Redis-backed suppression state:
    key = alarm:suppress:{alert_id}:{scope_id}:{suppress_idx}
    value = unix timestamp of active_until
    """

    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:suppress",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def get_active_until(
        self,
        *,
        alert_id: str,
        scope_id: str,
        suppress_idx: int,
    ) -> datetime | None:
        value = self._redis.get(
            f"{self._prefix}:{alert_id}:{scope_id}:suppress:{suppress_idx}"
        )
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            ts = float(value)
        except ValueError:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    def set_active_until(
        self,
        *,
        alert_id: str,
        scope_id: str,
        suppress_idx: int,
        active_until: datetime,
    ) -> None:
        active_until = _ensure_utc(active_until)
        ttl = max(
            1,
            int((active_until - datetime.now(timezone.utc)).total_seconds()),
        )
        self._redis.set(
            f"{self._prefix}:{alert_id}:{scope_id}:suppress:{suppress_idx}",
            str(active_until.timestamp()),
            ex=ttl,
        )

    def clear(
        self,
        *,
        alert_id: str,
        scope_id: str,
        suppress_idx: int,
    ) -> None:
        self._redis.delete(
            f"{self._prefix}:{alert_id}:{scope_id}:suppress:{suppress_idx}"
        )


class RedisDeferredWatchStore:
    """
    Redis-backed deferred watch state:
    key = alarm:deferred_watch:{alert_id}:{market_id}
    value json =
    {"target_liquidity_usd": ..., "expires_at": ..., "fired_at": ...}
    """

    def __init__(
        self,
        redis_client: RedisLike,
        prefix: str = "alarm:deferred_watch",
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix

    def load(
        self,
        *,
        alert_id: str,
        market_id: str,
    ) -> dict[str, float | str | None] | None:
        value = self._redis.get(f"{self._prefix}:{alert_id}:{market_id}")
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def save(
        self,
        *,
        alert_id: str,
        market_id: str,
        payload: dict[str, float | str | None],
        expires_at: datetime,
    ) -> None:
        expires_at = _ensure_utc(expires_at)
        ttl = max(
            1,
            int((expires_at - datetime.now(timezone.utc)).total_seconds()),
        )
        self._redis.set(
            f"{self._prefix}:{alert_id}:{market_id}",
            json.dumps(
                payload,
                separators=(",", ":"),
                sort_keys=True,
            ),
            ex=ttl,
        )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
