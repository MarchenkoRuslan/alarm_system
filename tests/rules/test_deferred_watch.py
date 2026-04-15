from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from alarm_system.rules.deferred_watch import InMemoryDeferredWatchStore
from alarm_system.rules_dsl import AlertRuleV1


def _deferred_rule() -> AlertRuleV1:
    return AlertRuleV1.model_validate(
        {
            "rule_id": "r-new-market-liq",
            "tenant_id": "tenant-1",
            "name": "New market liquidity",
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
        }
    )


class DeferredWatchStoreTests(unittest.TestCase):
    def test_arm_and_fire_once(self) -> None:
        now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        store = InMemoryDeferredWatchStore()
        rule = _deferred_rule()

        armed = store.arm(alert_id="a-1", market_id="m-1", rule=rule, armed_at=now)
        first_fire = store.check_and_fire(
            alert_id="a-1", market_id="m-1", liquidity_usd=120000.0, at=now + timedelta(minutes=10)
        )
        second_fire = store.check_and_fire(
            alert_id="a-1", market_id="m-1", liquidity_usd=150000.0, at=now + timedelta(minutes=11)
        )

        self.assertTrue(armed)
        self.assertTrue(first_fire)
        self.assertFalse(second_fire)
        state = store.get(alert_id="a-1", market_id="m-1")
        self.assertIsNotNone(state)
        self.assertTrue(state.is_fired)

    def test_expired_watch_does_not_fire(self) -> None:
        now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        store = InMemoryDeferredWatchStore()
        rule = _deferred_rule()

        store.arm(alert_id="a-1", market_id="m-1", rule=rule, armed_at=now)
        fired = store.check_and_fire(
            alert_id="a-1",
            market_id="m-1",
            liquidity_usd=120000.0,
            at=now + timedelta(hours=25),
        )
        self.assertFalse(fired)
        self.assertIsNone(store.get(alert_id="a-1", market_id="m-1"))
