from __future__ import annotations

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
from alarm_system.rules.runtime import RuleRuntime
from alarm_system.rules_dsl import AlertRuleV1


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


def _ruleset() -> list[RuleBinding]:
    rule_a = AlertRuleV1.model_validate(
        {
            "rule_id": "r-a",
            "tenant_id": "tenant-a",
            "name": "A-like",
            "rule_type": "trader_position_update",
            "version": 1,
            "expression": {
                "op": "OR",
                "children": [
                    {
                        "signal": "PositionOpened",
                        "op": "eq",
                        "threshold": 1,
                        "window": {"size_seconds": 60, "slide_seconds": 10},
                    },
                    {
                        "signal": "PositionClosed",
                        "op": "eq",
                        "threshold": 1,
                        "window": {"size_seconds": 60, "slide_seconds": 10},
                    },
                ],
            },
            "filters": {"category_tags": ["Politics"]},
        }
    )
    rule_b = AlertRuleV1.model_validate(
        {
            "rule_id": "r-b",
            "tenant_id": "tenant-a",
            "name": "B-like",
            "rule_type": "volume_spike_5m",
            "version": 2,
            "expression": {
                "op": "AND",
                "children": [
                    {
                        "signal": "price_return_5m_pct",
                        "op": "gte",
                        "threshold": 2.5,
                        "window": {"size_seconds": 300, "slide_seconds": 30},
                    },
                    {
                        "signal": "spread_bps",
                        "op": "lte",
                        "threshold": 120,
                        "window": {"size_seconds": 60, "slide_seconds": 10},
                    },
                ],
            },
            "filters": {"category_tags": ["Iran"]},
        }
    )
    rule_c = AlertRuleV1.model_validate(
        {
            "rule_id": "r-c",
            "tenant_id": "tenant-a",
            "name": "C-like",
            "rule_type": "new_market_liquidity",
            "version": 1,
            "expression": {
                "signal": "liquidity_usd",
                "op": "gte",
                "threshold": 100000,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
            "filters": {"category_tags": ["Politics"]},
            "deferred_watch": {
                "enabled": True,
                "target_liquidity_usd": 100000,
                "ttl_hours": 72,
            },
        }
    )
    return [
        RuleBinding(alert_id="alert-a", rule=rule_a),
        RuleBinding(alert_id="alert-b", rule=rule_b),
        RuleBinding(alert_id="alert-c", rule=rule_c),
    ]


def _collect_signatures(runtime: RuleRuntime, events: list[CanonicalEvent], bindings: list[RuleBinding]) -> list[str]:
    signatures: list[str] = []
    runtime.set_bindings(bindings)
    for event in events:
        decisions = runtime.evaluate_event(event=event)
        for decision in decisions:
            signatures.append(
                f"{decision.alert_id}:{decision.rule_id}:{decision.rule_version}:{decision.scope_id}:{decision.reason.summary}"
            )
    return signatures


class RuleRuntimeReplayTests(unittest.TestCase):
    def test_runtime_requires_bindings_before_evaluation(self) -> None:
        runtime = RuleRuntime()
        event = _event(
            event_type=EventType.TRADE,
            market_id="m-1",
            source_event_id="trade-1",
            event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            payload={"tags": ["iran"], "price_return_5m_pct": 3.0},
        )

        with self.assertRaises(RuntimeError):
            runtime.evaluate_event(event)

    def test_prefilter_is_built_once_during_bindings_load(self) -> None:
        bindings = _ruleset()
        runtime = RuleRuntime()
        event = _event(
            event_type=EventType.POSITION_UPDATE,
            market_id="m-pos",
            source_event_id="pos-1",
            event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            payload={"action": "open", "tags": ["politics"]},
        )

        runtime.set_bindings(bindings)
        bindings.clear()
        decisions = runtime.evaluate_event(event)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].alert_id, "alert-a")

    def test_tag_scoped_rule_does_not_trigger_when_event_has_no_tags(self) -> None:
        bindings = _ruleset()
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.POSITION_UPDATE,
                market_id="m-pos",
                source_event_id="pos-1",
                event_ts=base,
                payload={"action": "open"},
            )
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)
        self.assertEqual(signatures, [])

    def test_reference_a_b_c_rules_trigger_with_one_shot_delayed_liquidity(self) -> None:
        bindings = _ruleset()
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.POSITION_UPDATE,
                market_id="m-pos",
                source_event_id="pos-1",
                event_ts=base,
                payload={"action": "open", "tags": ["politics"]},
            ),
            _event(
                event_type=EventType.TRADE,
                market_id="m-iran",
                source_event_id="trade-1",
                event_ts=base + timedelta(seconds=1),
                payload={
                    "tags": ["iran"],
                    "price_return_5m_pct": 3.2,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.505", "100"]],
                },
            ),
            _event(
                event_type=EventType.MARKET_CREATED,
                market_id="m-new",
                source_event_id="new-1",
                event_ts=base + timedelta(seconds=2),
                payload={"tags": ["politics"], "question": "New market"},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-new",
                source_event_id="liq-1",
                event_ts=base + timedelta(seconds=3),
                payload={"tags": ["politics"], "liquidity_usd": 95000},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-new",
                source_event_id="liq-2",
                event_ts=base + timedelta(seconds=4),
                payload={"tags": ["politics"], "liquidity_usd": 120000},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-new",
                source_event_id="liq-2-dup",
                event_ts=base + timedelta(seconds=5),
                payload={"tags": ["politics"], "liquidity_usd": 130000},
            ),
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)

        self.assertEqual(len(signatures), 3)
        self.assertTrue(any(signature.startswith("alert-a:r-a:1:m-pos") for signature in signatures))
        self.assertTrue(any(signature.startswith("alert-b:r-b:2:m-iran") for signature in signatures))
        self.assertEqual(
            sum(1 for signature in signatures if signature.startswith("alert-c:r-c:1:m-new")),
            1,
        )

    def test_replay_parity_is_deterministic_under_duplicate_noise(self) -> None:
        bindings = _ruleset()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        canonical_window = [
            _event(
                event_type=EventType.MARKET_CREATED,
                market_id="m-new",
                source_event_id="new-1",
                event_ts=base + timedelta(seconds=1),
                payload={"tags": ["politics"]},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-new",
                source_event_id="liq-1",
                event_ts=base + timedelta(seconds=2),
                payload={"tags": ["politics"], "liquidity_usd": 120000},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-new",
                source_event_id="liq-dup",
                event_ts=base + timedelta(seconds=3),
                payload={"tags": ["politics"], "liquidity_usd": 130000},
            ),
        ]
        replay_with_noise = [
            canonical_window[0],
            canonical_window[1],
            canonical_window[1],  # duplicate replay noise
            canonical_window[2],
        ]

        first = _collect_signatures(
            runtime=RuleRuntime(),
            events=canonical_window,
            bindings=bindings,
        )
        second = _collect_signatures(
            runtime=RuleRuntime(),
            events=replay_with_noise,
            bindings=bindings,
        )

        self.assertEqual(first, second)
