from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alarm_system.entities import (
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    RedisDeliveryAttemptStore,
)


class _RecordingRedis:
    """Minimal Redis-like stub that records calls for assertions."""

    def __init__(self) -> None:
        self.set_calls: list[dict] = []
        self.rpush_calls: list[tuple[str, tuple[str, ...]]] = []
        self.ltrim_calls: list[tuple[str, int, int]] = []
        self.ltrim_should_raise = False

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self.set_calls.append(
            {"key": key, "value": value, "ex": ex, "nx": nx}
        )
        return True

    def rpush(self, key: str, *values: str) -> int:
        self.rpush_calls.append((key, values))
        return len(values)

    def ltrim(self, key: str, start: int, end: int) -> None:
        if self.ltrim_should_raise:
            raise RuntimeError("boom")
        self.ltrim_calls.append((key, start, end))

    def get(self, key: str) -> str | None:
        return None

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return []


def _attempt(attempt_id: str = "att-1", alert_id: str = "a-1") -> DeliveryAttempt:
    return DeliveryAttempt.model_validate(
        {
            "attempt_id": attempt_id,
            "trigger_id": "tr-1",
            "alert_id": alert_id,
            "channel": DeliveryChannel.TELEGRAM,
            "destination": "500",
            "status": DeliveryStatus.SENT,
            "enqueued_at": datetime(2026, 4, 16, tzinfo=timezone.utc),
        }
    )


class RedisDeliveryAttemptStoreRetentionTests(unittest.TestCase):
    def test_save_sets_ttl_and_trims_main_index(self) -> None:
        redis = _RecordingRedis()
        store = RedisDeliveryAttemptStore(
            redis,
            attempt_ttl_seconds=123,
            main_index_max_len=7,
        )

        store.save(_attempt())

        self.assertEqual(len(redis.set_calls), 1)
        self.assertEqual(redis.set_calls[0]["ex"], 123)
        self.assertEqual(
            redis.ltrim_calls,
            [("alarm:delivery_attempt:index", -7, -1)],
        )

    def test_save_for_user_trims_both_indexes(self) -> None:
        redis = _RecordingRedis()
        store = RedisDeliveryAttemptStore(
            redis,
            main_index_max_len=7,
            user_index_max_len=3,
        )

        store.save_for_user(_attempt(), user_id="42")

        self.assertEqual(
            redis.rpush_calls,
            [
                ("alarm:delivery_attempt:index", ("att-1",)),
                ("alarm:delivery_attempt:by_user:42", ("att-1",)),
            ],
        )
        self.assertEqual(
            redis.ltrim_calls,
            [
                ("alarm:delivery_attempt:index", -7, -1),
                ("alarm:delivery_attempt:by_user:42", -3, -1),
            ],
        )

    def test_ltrim_failure_does_not_break_save(self) -> None:
        redis = _RecordingRedis()
        redis.ltrim_should_raise = True
        store = RedisDeliveryAttemptStore(redis)

        store.save_for_user(_attempt(), user_id="42")

        self.assertEqual(len(redis.set_calls), 1)
        self.assertEqual(len(redis.rpush_calls), 2)
        # ltrim raised on every call, so the list stays empty.
        self.assertEqual(redis.ltrim_calls, [])


class InMemoryDeliveryAttemptStoreCapTests(unittest.TestCase):
    def test_per_user_list_caps_at_configured_max(self) -> None:
        store = InMemoryDeliveryAttemptStore(user_index_max_len=3)
        for i in range(5):
            store.save_for_user(_attempt(attempt_id=f"att-{i}"), user_id="42")

        history = store.list_by_user(user_id="42", limit=10)
        self.assertEqual(
            [item.attempt_id for item in history],
            ["att-4", "att-3", "att-2"],
        )
