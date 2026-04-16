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
from alarm_system.compute.prefilter import PrefilterIndex, RuleBinding
from alarm_system.rules_dsl import AlertRuleV1, RuleType


def _rule(
    rule_id: str,
    rule_type: RuleType,
    tags: list[str] | None = None,
) -> AlertRuleV1:
    return AlertRuleV1.model_validate(
        {
            "rule_id": rule_id,
            "tenant_id": "tenant-a",
            "name": rule_id,
            "rule_type": rule_type.value,
            "version": 1,
            "expression": {
                "signal": "price_return_1m_pct",
                "op": "gte",
                "threshold": 1.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
            "filters": {"category_tags": tags or []},
        }
    )


def _event(event_type: EventType, payload: dict[str, object]) -> CanonicalEvent:
    now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    payload_hash = build_payload_hash(payload)
    source_event_id = f"{event_type.value}-src-1"
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=event_type,
            market_id="mkt-1",
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=MarketRef(market_id="mkt-1"),
        event_ts=now,
        ingested_ts=now,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(correlation_id=source_event_id, partition_key="mkt-1"),
    )


class PrefilterIndexTests(unittest.TestCase):
    def test_lookup_respects_event_type_and_tag(self) -> None:
        index = PrefilterIndex().build(
            [
                RuleBinding(
                    alert_id="a-trade-politics",
                    rule=_rule("r-trade-politics", RuleType.VOLUME_SPIKE_5M, ["Politics"]),
                ),
                RuleBinding(
                    alert_id="a-trade-wildcard",
                    rule=_rule("r-trade-any", RuleType.VOLUME_SPIKE_5M, []),
                ),
                RuleBinding(
                    alert_id="a-position",
                    rule=_rule("r-position", RuleType.TRADER_POSITION_UPDATE, ["Politics"]),
                ),
            ]
        )

        trade_event = _event(EventType.TRADE, {"market_id": "mkt-1", "tags": ["politics"]})
        candidates = index.lookup(trade_event)
        candidate_ids = sorted(binding.alert_id for binding in candidates)

        self.assertEqual(candidate_ids, ["a-trade-politics", "a-trade-wildcard"])

    def test_missing_event_tags_returns_all_bucket_candidates(self) -> None:
        index = PrefilterIndex().build(
            [
                RuleBinding(
                    alert_id="a-trade-politics",
                    rule=_rule("r-trade-politics", RuleType.VOLUME_SPIKE_5M, ["Politics"]),
                ),
                RuleBinding(
                    alert_id="a-trade-crypto",
                    rule=_rule("r-trade-crypto", RuleType.VOLUME_SPIKE_5M, ["Crypto"]),
                ),
                RuleBinding(
                    alert_id="a-trade-any",
                    rule=_rule("r-trade-any", RuleType.VOLUME_SPIKE_5M, []),
                ),
            ]
        )

        # False-negative guard: missing tags should not discard tagged rules.
        event_without_tags = _event(EventType.TRADE, {"market_id": "mkt-1"})
        candidates = index.lookup(event_without_tags)
        candidate_ids = sorted(binding.alert_id for binding in candidates)

        self.assertEqual(candidate_ids, ["a-trade-any", "a-trade-crypto", "a-trade-politics"])

    def test_total_bindings_cache_matches_uncached(self) -> None:
        index = PrefilterIndex().build(
            [
                RuleBinding(
                    alert_id="a-trade-politics",
                    rule=_rule("r-trade-politics", RuleType.VOLUME_SPIKE_5M, ["Politics"]),
                ),
                RuleBinding(
                    alert_id="a-trade-wildcard",
                    rule=_rule("r-trade-any", RuleType.VOLUME_SPIKE_5M, []),
                ),
                RuleBinding(
                    alert_id="a-position",
                    rule=_rule("r-position", RuleType.TRADER_POSITION_UPDATE, ["Politics"]),
                ),
            ]
        )
        self.assertIsNotNone(index._totals_by_event_type)
        for event_type in EventType:
            self.assertEqual(
                index.total_bindings_for_event(event_type),
                index._totals_for_event_type_uncached(event_type),
            )
