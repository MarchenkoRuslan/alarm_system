"""Integration tests: ``filters_json`` on ``RuleBinding`` gates triggers."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

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
from alarm_system.rules.runtime import RuleRuntime
from alarm_system.rules_dsl import AlertRuleV1


def _event(
    *,
    market_id: str,
    source_event_id: str,
    event_ts: datetime,
    payload: dict[str, object],
) -> CanonicalEvent:
    payload_hash = build_payload_hash(payload)
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=EventType.TRADE,
            market_id=market_id,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=EventType.TRADE,
        market_ref=MarketRef(market_id=market_id),
        event_ts=event_ts,
        ingested_ts=event_ts,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(correlation_id=source_event_id, partition_key=market_id),
    )


def _volume_rule() -> AlertRuleV1:
    return AlertRuleV1.model_validate(
        {
            "rule_id": "r-filter-int",
            "tenant_id": "tenant-a",
            "name": "int",
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


class RuntimeAlertFiltersIntegrationTests(unittest.TestCase):
    def test_high_liquidity_min_blocks_trigger(self) -> None:
        runtime = RuleRuntime()
        runtime.set_bindings(
            [
                RuleBinding(
                    alert_id="a-1",
                    rule=_volume_rule(),
                    filters_json={"liquidity_usd_min": 500_000.0},
                )
            ]
        )
        now = datetime.now(timezone.utc)
        ev = _event(
            market_id="m-1",
            source_event_id="t-1",
            event_ts=now,
            payload={
                "price_return_1m_pct": 2.0,
                "liquidity_usd": 100_000.0,
            },
        )
        self.assertEqual(len(runtime.evaluate_event(ev)), 0)

    def test_lower_liquidity_min_allows_trigger(self) -> None:
        runtime = RuleRuntime()
        runtime.set_bindings(
            [
                RuleBinding(
                    alert_id="a-1",
                    rule=_volume_rule(),
                    filters_json={"liquidity_usd_min": 50_000.0},
                )
            ]
        )
        now = datetime.now(timezone.utc)
        ev = _event(
            market_id="m-1",
            source_event_id="t-2",
            event_ts=now,
            payload={
                "price_return_1m_pct": 2.0,
                "liquidity_usd": 100_000.0,
            },
        )
        self.assertEqual(len(runtime.evaluate_event(ev)), 1)

    def test_matched_filters_include_alert_require_event_tag(self) -> None:
        runtime = RuleRuntime()
        runtime.set_bindings(
            [
                RuleBinding(
                    alert_id="a-req",
                    rule=_volume_rule(),
                    filters_json={"require_event_tag": "breaking"},
                )
            ]
        )
        now = datetime.now(timezone.utc)
        ev = _event(
            market_id="m-1",
            source_event_id="t-req",
            event_ts=now,
            payload={
                "tags": ["breaking"],
                "price_return_1m_pct": 2.0,
            },
        )
        decisions = runtime.evaluate_event(ev)
        self.assertEqual(len(decisions), 1)
        mf = decisions[0].reason.matched_filters
        self.assertEqual(mf.get("require_event_tag"), "breaking")
