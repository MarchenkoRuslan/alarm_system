from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from alarm_system.canonical_event import (
    CanonicalEvent,
    EventType,
    MarketRef,
    Source,
    TraceContext,
    build_event_id,
    build_payload_hash,
)
from alarm_system.compute.prefilter import RuleBinding
from alarm_system.entities import DeliveryAttempt, DeliveryChannel, DeliveryStatus
from alarm_system.rules.deferred_watch import RedisBackedDeferredWatchStore
from alarm_system.rules.runtime import RuleRuntime
from alarm_system.rules.suppression import RedisSuppressionStore
from alarm_system.rules_dsl import AlertRuleV1, TriggerReason
from alarm_system.state import (
    RedisDeliveryAttemptStore,
    RedisCooldownStore,
    RedisDeferredWatchStore,
    RedisSuppressionStateStore,
    RedisTriggerAuditStore,
    RedisTriggerDedupStore,
    TriggerAuditRecord,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, tuple[str, datetime | None]] = {}
        self._lists: dict[str, list[str]] = {}

    def get(self, key: str) -> str | None:
        value = self._store.get(key)
        if value is None:
            return None
        raw, expires = value
        if expires is not None and datetime.now(timezone.utc) >= expires:
            self._store.pop(key, None)
            return None
        return raw

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and self.get(key) is not None:
            return False
        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ex)
            if ex is not None
            else None
        )
        self._store[key] = (value, expires)
        return True

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def rpush(self, key: str, *values: str) -> int:
        bucket = self._lists.setdefault(key, [])
        for value in values:
            bucket.append(value)
        return len(bucket)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        bucket = self._lists.get(key, [])
        if not bucket:
            return []
        start_idx = max(0, start)
        end_idx = len(bucket) - 1 if end < 0 else min(end, len(bucket) - 1)
        if end_idx < start_idx:
            return []
        return bucket[start_idx : end_idx + 1]


class _IndexOpsTrackingRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.rpush_calls: list[tuple[str, tuple[str, ...]]] = []

    def rpush(self, key: str, *values: str) -> int:
        self.rpush_calls.append((key, values))
        return super().rpush(key, *values)


class _StrictExpireRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.set_calls = 0

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        self.set_calls += 1
        if ex is not None and ex <= 0:
            raise ValueError("ERR invalid expire time in 'set' command")
        return super().set(key=key, value=value, ex=ex, nx=nx)


def _event(
    event_type: EventType,
    market_id: str,
    source_event_id: str,
    event_ts: datetime,
    payload: dict[str, object],
) -> CanonicalEvent:
    payload_hash = build_payload_hash(payload)
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=event_type,
            market_id=market_id,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=MarketRef(market_id=market_id),
        event_ts=event_ts,
        ingested_ts=event_ts,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(correlation_id=source_event_id, partition_key=market_id),
    )


class Phase3StateTests(unittest.TestCase):
    def test_redis_dedup_and_cooldown_use_nx_ttl_contract(self) -> None:
        redis = _FakeRedis()
        dedup = RedisTriggerDedupStore(redis)
        cooldown = RedisCooldownStore(redis)
        at = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)

        first_ok, key = dedup.reserve(
            tenant_id="tenant-a",
            rule_id="r-1",
            rule_version=1,
            scope_id="m-1",
            event_time=at,
            bucket_seconds=60,
            ttl_seconds=65,
        )
        second_ok, key2 = dedup.reserve(
            tenant_id="tenant-a",
            rule_id="r-1",
            rule_version=1,
            scope_id="m-1",
            event_time=at,
            bucket_seconds=60,
            ttl_seconds=65,
        )

        self.assertTrue(first_ok)
        self.assertFalse(second_ok)
        self.assertEqual(key, key2)

        first_channel = cooldown.allow(
            tenant_id="tenant-a",
            rule_id="r-1",
            rule_version=1,
            scope_id="m-1",
            channel=DeliveryChannel.TELEGRAM,
            triggered_at=at,
            cooldown_seconds=30,
        )
        second_channel = cooldown.allow(
            tenant_id="tenant-a",
            rule_id="r-1",
            rule_version=1,
            scope_id="m-1",
            channel=DeliveryChannel.TELEGRAM,
            triggered_at=at,
            cooldown_seconds=30,
        )
        self.assertTrue(first_channel)
        self.assertFalse(second_channel)

    def test_runtime_with_redis_backed_watch_and_suppression(self) -> None:
        redis = _FakeRedis()
        runtime = RuleRuntime(
            dedup=RedisTriggerDedupStore(redis),
            deferred_watches=RedisBackedDeferredWatchStore(RedisDeferredWatchStore(redis)),
            suppression=RedisSuppressionStore(RedisSuppressionStateStore(redis)),
        )
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-phase3",
                "tenant_id": "tenant-a",
                "name": "Phase3",
                "rule_type": "new_market_liquidity",
                "version": 1,
                "expression": {
                    "signal": "liquidity_usd",
                    "op": "gte",
                    "threshold": 100000,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
                "deferred_watch": {
                    "enabled": True,
                    "target_liquidity_usd": 100000,
                    "ttl_hours": 24,
                },
                "suppress_if": [
                    {
                        "signal": "spread_bps",
                        "op": "gte",
                        "threshold": 200,
                        "duration_seconds": 10,
                    }
                ],
            }
        )
        runtime.set_bindings([RuleBinding(alert_id="alert-phase3", rule=rule)])
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.MARKET_CREATED,
                market_id="m-phase3",
                source_event_id="new",
                event_ts=base,
                payload={"tags": ["politics"]},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-phase3",
                source_event_id="liq-1",
                event_ts=base + timedelta(seconds=1),
                payload={
                    "liquidity_usd": 120000,
                    "bids": [["0.5", "100"]],
                    "asks": [["0.53", "100"]],
                },
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-phase3",
                source_event_id="liq-2",
                event_ts=base + timedelta(seconds=2),
                payload={
                    "liquidity_usd": 130000,
                    "bids": [["0.5", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-phase3",
                source_event_id="liq-3",
                event_ts=base + timedelta(seconds=12),
                payload={
                    "liquidity_usd": 130000,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-phase3",
                source_event_id="liq-4",
                event_ts=base + timedelta(seconds=13),
                payload={
                    "liquidity_usd": 140000,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
        ]
        first = runtime.evaluate_event(events[0])
        second = runtime.evaluate_event(events[1])
        third = runtime.evaluate_event(events[2])
        fourth = runtime.evaluate_event(events[3])
        fifth = runtime.evaluate_event(events[4])
        self.assertEqual(first, [])
        self.assertEqual(len(second), 0)  # suppressed by spread_bps >= 200
        self.assertEqual(len(third), 0)  # still suppressed and watch remains armed
        self.assertEqual(len(fourth), 1)  # first non-suppressed crossing delivers
        self.assertEqual(len(fifth), 0)  # one-shot watch now fired

    def test_redis_deferred_watch_payload_is_json(self) -> None:
        redis = _FakeRedis()
        state = RedisDeferredWatchStore(redis)
        expires = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
        state.save(
            alert_id="a1",
            market_id="m1",
            payload={"target_liquidity_usd": 100000.0, "fired_at": None},
            expires_at=expires,
        )
        raw = redis.get("alarm:deferred_watch:a1:m1")
        self.assertIsNotNone(raw)
        decoded = json.loads(raw or "{}")
        self.assertEqual(decoded["target_liquidity_usd"], 100000.0)

    def test_redis_cooldown_zero_seconds_does_not_call_set(self) -> None:
        redis = _StrictExpireRedis()
        cooldown = RedisCooldownStore(redis)
        allowed = cooldown.allow(
            tenant_id="tenant-a",
            rule_id="r-1",
            rule_version=1,
            scope_id="m-1",
            channel=DeliveryChannel.TELEGRAM,
            triggered_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            cooldown_seconds=0,
        )

        self.assertTrue(allowed)
        self.assertEqual(redis.set_calls, 0)

    def test_redis_trigger_audit_store_save_once_semantics(self) -> None:
        redis = _FakeRedis()
        store = RedisTriggerAuditStore(redis)
        reason = TriggerReason.model_validate(
            {
                "rule_id": "r-1",
                "rule_version": 1,
                "evaluated_at": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
                "predicates": [],
                "summary": "summary",
            }
        )
        record = TriggerAuditRecord(
            trigger_id="t-1",
            trigger_key="k-1",
            alert_id="a-1",
            rule_id="r-1",
            rule_version=1,
            tenant_id="tenant-a",
            scope_id="m-1",
            reason=reason,
            event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            evaluated_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        )
        first = store.save_once(record)
        second = store.save_once(record)

        self.assertTrue(first)
        self.assertFalse(second)
        saved = store.all()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].trigger_key, "k-1")

    def test_redis_trigger_audit_index_uses_atomic_rpush(self) -> None:
        redis = _IndexOpsTrackingRedis()
        store = RedisTriggerAuditStore(redis)
        reason = TriggerReason.model_validate(
            {
                "rule_id": "r-1",
                "rule_version": 1,
                "evaluated_at": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
                "predicates": [],
                "summary": "summary",
            }
        )
        store.save_once(
            TriggerAuditRecord(
                trigger_id="t-atomic",
                trigger_key="k-atomic",
                alert_id="a-1",
                rule_id="r-1",
                rule_version=1,
                tenant_id="tenant-a",
                scope_id="m-1",
                reason=reason,
                event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
                evaluated_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            )
        )

        self.assertIn(
            ("alarm:trigger_audit:index", ("k-atomic",)),
            redis.rpush_calls,
        )

    def test_redis_delivery_attempt_store_persists_attempts(self) -> None:
        redis = _FakeRedis()
        store = RedisDeliveryAttemptStore(redis)
        attempt = DeliveryAttempt.model_validate(
            {
                "attempt_id": "att-1",
                "trigger_id": "tr-1",
                "alert_id": "a-1",
                "channel": "telegram",
                "destination": "12345",
                "status": DeliveryStatus.RETRYING,
                "attempt_no": 1,
                "error_code": "temporary",
                "error_detail": "temporary",
                "enqueued_at": datetime(
                    2026, 4, 16, 12, 0, tzinfo=timezone.utc
                ),
            }
        )
        store.save(attempt)
        saved = store.all()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].attempt_id, "att-1")

    def test_redis_delivery_attempt_index_uses_atomic_rpush(self) -> None:
        redis = _IndexOpsTrackingRedis()
        store = RedisDeliveryAttemptStore(redis)
        store.save(
            DeliveryAttempt.model_validate(
                {
                    "attempt_id": "att-atomic",
                    "trigger_id": "tr-1",
                    "alert_id": "a-1",
                    "channel": "telegram",
                    "destination": "12345",
                    "status": DeliveryStatus.RETRYING,
                    "attempt_no": 1,
                    "enqueued_at": datetime(
                        2026, 4, 16, 12, 0, tzinfo=timezone.utc
                    ),
                }
            )
        )

        self.assertIn(
            ("alarm:delivery_attempt:index", ("att-atomic",)),
            redis.rpush_calls,
        )
