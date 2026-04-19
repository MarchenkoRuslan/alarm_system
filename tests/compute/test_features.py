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
from alarm_system.compute.features import extract_feature_snapshot


def _event(payload: dict[str, object], event_type: EventType = EventType.TRADE) -> CanonicalEvent:
    now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    payload_hash = build_payload_hash(payload)
    source_event_id = "src-1"
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=event_type,
            market_id="m-1",
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=MarketRef(market_id="m-1"),
        event_ts=now,
        ingested_ts=now,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(correlation_id=source_event_id, partition_key="m-1"),
    )


class FeatureExtractionTests(unittest.TestCase):
    def test_normalizes_tags_from_string_and_dict_payload(self) -> None:
        snapshot = extract_feature_snapshot(
            _event(
                {
                    "tags": [" Politics ", {"name": "Esports"}, {"label": "esports"}],
                }
            )
        )
        self.assertEqual(snapshot.tags, ["esports", "politics"])

    def test_uses_category_fallback_when_tags_absent(self) -> None:
        snapshot = extract_feature_snapshot(_event({"category": " Crypto "}))
        self.assertEqual(snapshot.tags, ["crypto"])

    def test_extracts_alias_fields_and_delta_conversion(self) -> None:
        snapshot = extract_feature_snapshot(
            _event(
                {
                    "delta": "0.02",
                    "liquidity": "100000",
                    "smartScore": "88",
                    "accountAgeDays": "365",
                }
            )
        )
        self.assertAlmostEqual(snapshot.values["price_return_1m_pct"], 2.0)
        self.assertEqual(snapshot.values["liquidity_usd"], 100000.0)
        self.assertEqual(snapshot.values["smart_score"], 88.0)
        self.assertEqual(snapshot.values["account_age_days"], 365.0)

    def test_computes_spread_and_imbalance_from_orderbook(self) -> None:
        snapshot = extract_feature_snapshot(
            _event(
                {
                    "bids": [["0.50", "100"], ["0.49", "30"]],
                    "asks": [["0.55", "50"], ["0.56", "20"]],
                }
            )
        )
        self.assertIn("spread_bps", snapshot.values)
        self.assertIn("book_imbalance_topN", snapshot.values)
        self.assertGreater(snapshot.values["spread_bps"], 0.0)
        self.assertGreater(snapshot.values["book_imbalance_topN"], 0.0)

    def test_extracts_position_signals_for_position_updates(self) -> None:
        snapshot = extract_feature_snapshot(
            _event(
                {"action": "increase"},
                event_type=EventType.POSITION_UPDATE,
            )
        )
        self.assertEqual(snapshot.values.get("PositionIncreased"), 1.0)
