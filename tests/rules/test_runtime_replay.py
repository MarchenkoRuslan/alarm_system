from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_RECORDED_REPLAY_FIXTURE = _FIXTURES_DIR / "replay_window.json"


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


def _recorded_parity_ruleset() -> list[RuleBinding]:
    rule = AlertRuleV1.model_validate(
        {
            "rule_id": "r-recorded-parity",
            "tenant_id": "tenant-a",
            "name": "Recorded parity",
            "rule_type": "volume_spike_5m",
            "version": 1,
            "expression": {
                "signal": "price_return_1m_pct",
                "op": "gte",
                "threshold": 2.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
        }
    )
    return [RuleBinding(alert_id="alert-recorded-parity", rule=rule)]


def _collect_signatures(
    runtime: RuleRuntime,
    events: list[CanonicalEvent],
    bindings: list[RuleBinding],
) -> list[str]:
    signatures: list[str] = []
    runtime.set_bindings(bindings)
    for event in events:
        decisions = runtime.evaluate_event(event=event)
        for decision in decisions:
            signatures.append(
                f"{decision.alert_id}:{decision.rule_id}:"
                f"{decision.rule_version}:{decision.scope_id}:{decision.reason.summary}"
            )
    return signatures


def _load_recorded_replay_window(base: datetime) -> list[CanonicalEvent]:
    raw = json.loads(_RECORDED_REPLAY_FIXTURE.read_text(encoding="utf-8"))
    events: list[CanonicalEvent] = []
    for item in raw["events"]:
        event_type = EventType(item["event_type"])
        event_ts = base + timedelta(seconds=int(item["offset_seconds"]))
        events.append(
            _event(
                event_type=event_type,
                market_id=item["market_id"],
                source_event_id=item["source_event_id"],
                event_ts=event_ts,
                payload=item["payload"],
            )
        )
    return events


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

    def test_runtime_applies_min_score_and_account_age_filters(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-filtered",
                "tenant_id": "tenant-a",
                "name": "Filtered",
                "rule_type": "trader_position_update",
                "version": 1,
                "expression": {
                    "signal": "PositionOpened",
                    "op": "eq",
                    "threshold": 1,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
                "filters": {
                    "category_tags": ["Politics"],
                    "min_smart_score": 80,
                    "min_account_age_days": 365,
                },
            }
        )
        bindings = [RuleBinding(alert_id="alert-filtered", rule=rule)]
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.POSITION_UPDATE,
                market_id="m-pos",
                source_event_id="pos-low-score",
                event_ts=base,
                payload={
                    "action": "open",
                    "tags": ["politics"],
                    "smart_score": 70,
                    "account_age_days": 500,
                },
            ),
            _event(
                event_type=EventType.POSITION_UPDATE,
                market_id="m-pos",
                source_event_id="pos-low-age",
                event_ts=base + timedelta(seconds=1),
                payload={
                    "action": "open",
                    "tags": ["politics"],
                    "smart_score": 90,
                    "account_age_days": 100,
                },
            ),
            _event(
                event_type=EventType.POSITION_UPDATE,
                market_id="m-pos",
                source_event_id="pos-pass",
                event_ts=base + timedelta(seconds=2),
                payload={
                    "action": "open",
                    "tags": ["politics"],
                    "smart_score": 92,
                    "account_age_days": 420,
                },
            ),
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)
        self.assertEqual(len(signatures), 1)
        self.assertTrue(signatures[0].startswith("alert-filtered:r-filtered:1:m-pos"))

    def test_runtime_applies_iran_tag_only_filter(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-iran-only",
                "tenant_id": "tenant-a",
                "name": "Iran only",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_5m_pct",
                    "op": "gte",
                    "threshold": 2.5,
                    "window": {"size_seconds": 300, "slide_seconds": 30},
                },
                "filters": {"iran_tag_only": True},
            }
        )
        bindings = [RuleBinding(alert_id="alert-iran-only", rule=rule)]
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.TRADE,
                market_id="m-politics",
                source_event_id="trade-politics",
                event_ts=base,
                payload={"tags": ["politics"], "price_return_5m_pct": 3.0},
            ),
            _event(
                event_type=EventType.TRADE,
                market_id="m-iran",
                source_event_id="trade-iran",
                event_ts=base + timedelta(seconds=1),
                payload={"tags": ["iran"], "price_return_5m_pct": 3.0},
            ),
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)
        self.assertEqual(len(signatures), 1)
        self.assertTrue(signatures[0].startswith("alert-iran-only:r-iran-only:1:m-iran"))

    def test_suppress_if_blocks_within_duration_then_allows_trigger(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-suppress-window",
                "tenant_id": "tenant-a",
                "name": "Suppress window",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_5m_pct",
                    "op": "gte",
                    "threshold": 2.5,
                    "window": {"size_seconds": 300, "slide_seconds": 30},
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
        bindings = [RuleBinding(alert_id="alert-suppress-window", rule=rule)]
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.TRADE,
                market_id="m-suppress",
                source_event_id="s-1",
                event_ts=base,
                payload={
                    "price_return_5m_pct": 3.0,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.53", "100"]],
                },
            ),
            _event(
                event_type=EventType.TRADE,
                market_id="m-suppress",
                source_event_id="s-2",
                event_ts=base + timedelta(seconds=9),
                payload={
                    "price_return_5m_pct": 3.0,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
            _event(
                event_type=EventType.TRADE,
                market_id="m-suppress",
                source_event_id="s-3",
                event_ts=base + timedelta(seconds=10),
                payload={
                    "price_return_5m_pct": 3.0,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)
        self.assertEqual(len(signatures), 1)
        self.assertTrue(
            signatures[0].startswith("alert-suppress-window:r-suppress-window:1:m-suppress")
        )

    def test_suppress_if_missing_signal_does_not_block_trigger(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-suppress-missing",
                "tenant_id": "tenant-a",
                "name": "Suppress missing",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_5m_pct",
                    "op": "gte",
                    "threshold": 2.5,
                    "window": {"size_seconds": 300, "slide_seconds": 30},
                },
                "suppress_if": [
                    {
                        "signal": "non_existing_signal",
                        "op": "gte",
                        "threshold": 1,
                        "duration_seconds": 30,
                    }
                ],
            }
        )
        bindings = [RuleBinding(alert_id="alert-suppress-missing", rule=rule)]
        runtime = RuleRuntime()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.TRADE,
                market_id="m-suppress-missing",
                source_event_id="s-missing",
                event_ts=base,
                payload={
                    "price_return_5m_pct": 3.0,
                    "bids": [["0.50", "100"]],
                    "asks": [["0.505", "100"]],
                },
            )
        ]

        signatures = _collect_signatures(runtime=runtime, events=events, bindings=bindings)
        self.assertEqual(len(signatures), 1)
        self.assertTrue(
            signatures[0].startswith(
                "alert-suppress-missing:r-suppress-missing:1:m-suppress-missing"
            )
        )

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
        self.assertTrue(
            any(signature.startswith("alert-a:r-a:1:m-pos") for signature in signatures)
        )
        self.assertTrue(
            any(signature.startswith("alert-b:r-b:2:m-iran") for signature in signatures)
        )
        self.assertEqual(
            sum(1 for signature in signatures if signature.startswith("alert-c:r-c:1:m-new")),
            1,
        )

    def test_replay_parity_is_deterministic_under_duplicate_noise(self) -> None:
        bindings = _recorded_parity_ruleset()
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        canonical_window = _load_recorded_replay_window(base=base)
        replay_with_noise = [
            canonical_window[0],
            canonical_window[1],
            canonical_window[1],  # duplicate replay noise on non-triggering event
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
        self.assertEqual(len(first), 1)
        self.assertTrue(
            first[0].startswith("alert-recorded-parity:r-recorded-parity:1:mkt-1")
        )

    def test_runtime_dedup_drops_replayed_trigger_within_same_bucket(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-dedup",
                "tenant_id": "tenant-a",
                "name": "Dedup bucket",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_1m_pct",
                    "op": "gte",
                    "threshold": 2.0,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
            }
        )
        runtime = RuleRuntime()
        runtime.set_bindings([RuleBinding(alert_id="alert-dedup", rule=rule)])
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        event = _event(
            event_type=EventType.TRADE,
            market_id="m-dedup",
            source_event_id="trade-dedup-1",
            event_ts=base,
            payload={"price_return_1m_pct": 2.4},
        )

        first = runtime.evaluate_event(event)
        second = runtime.evaluate_event(event)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_deferred_watch_remains_armed_during_suppression_window(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-watch",
                "tenant_id": "tenant-a",
                "name": "Watch suppression",
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
        runtime = RuleRuntime()
        runtime.set_bindings([RuleBinding(alert_id="alert-watch", rule=rule)])
        base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(
                event_type=EventType.MARKET_CREATED,
                market_id="m-watch",
                source_event_id="new-watch",
                event_ts=base,
                payload={},
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-watch",
                source_event_id="liq-watch-1",
                event_ts=base + timedelta(seconds=1),
                payload={
                    "liquidity_usd": 120000,
                    "bids": [["0.5", "100"]],
                    "asks": [["0.53", "100"]],
                },
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-watch",
                source_event_id="liq-watch-2",
                event_ts=base + timedelta(seconds=12),
                payload={
                    "liquidity_usd": 130000,
                    "bids": [["0.5", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
            _event(
                event_type=EventType.LIQUIDITY_UPDATE,
                market_id="m-watch",
                source_event_id="liq-watch-3",
                event_ts=base + timedelta(seconds=13),
                payload={
                    "liquidity_usd": 135000,
                    "bids": [["0.5", "100"]],
                    "asks": [["0.51", "100"]],
                },
            ),
        ]
        signatures = _collect_signatures(
            runtime=runtime,
            events=events,
            bindings=[RuleBinding(alert_id="alert-watch", rule=rule)],
        )
        self.assertEqual(
            sum(
                1
                for item in signatures
                if item.startswith("alert-watch:r-watch:1:m-watch")
            ),
            1,
        )
