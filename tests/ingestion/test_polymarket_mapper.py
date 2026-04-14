from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from alarm_system.adapters import AdapterEnvelope, MarketSource
from alarm_system.canonical_event import EventType
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter
from alarm_system.ingestion.polymarket.mapper import (
    MappingContext,
    UnsupportedPayloadError,
    map_polymarket_payload,
)
from alarm_system.ingestion.validation import validate_canonical_event

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket"
RECEIVED_AT = datetime(2026, 4, 14, 10, 10, tzinfo=timezone.utc)


def _load_fixture(name: str) -> dict[str, object]:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class PolymarketMapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_core_event_types_to_canonical(self) -> None:
        expected_types = {
            "book.json": EventType.ORDERBOOK_DELTA,
            "price_change.json": EventType.MARKET_SNAPSHOT,
            "last_trade_price.json": EventType.TRADE,
            "new_market.json": EventType.MARKET_CREATED,
            "market_resolved.json": EventType.MARKET_RESOLVED,
        }
        context = MappingContext(adapter_version="test@1")

        for filename, expected in expected_types.items():
            payload = _load_fixture(filename)
            event = map_polymarket_payload(payload=payload, received_at=RECEIVED_AT, context=context)

            self.assertEqual(event.event_type, expected)
            self.assertEqual(event.trace.adapter_version, "test@1")
            validate_canonical_event(event)

    async def test_adapter_normalize_is_deterministic_for_same_envelope(self) -> None:
        adapter = PolymarketMarketAdapter()
        payload = _load_fixture("book.json")
        envelope = AdapterEnvelope(
            source=MarketSource.POLYMARKET,
            payload=payload,
            received_at=RECEIVED_AT,
        )

        first = await adapter.normalize(envelope)
        second = await adapter.normalize(envelope)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(
            first[0].model_dump(mode="json"),
            second[0].model_dump(mode="json"),
        )

    async def test_deterministic_fallback_source_event_id_without_explicit_id(self) -> None:
        context = MappingContext(adapter_version="test@1")
        payload = {
            "type": "book",
            "market_id": "mkt-x",
            "asset_id": "asset-x",
            "bids": [["0.4", "10"]],
            "asks": [["0.6", "5"]],
        }

        first = map_polymarket_payload(payload=payload, received_at=RECEIVED_AT, context=context)
        second = map_polymarket_payload(payload=payload, received_at=RECEIVED_AT, context=context)

        self.assertEqual(first.source_event_id, second.source_event_id)
        self.assertEqual(first.event_id, second.event_id)

    async def test_event_id_changes_when_payload_changes(self) -> None:
        context = MappingContext(adapter_version="test@1")
        payload_a = {
            "type": "book",
            "event_id": "evt-1",
            "market_id": "mkt-x",
            "asset_id": "asset-x",
            "bids": [["0.4", "10"]],
            "asks": [["0.6", "5"]],
        }
        payload_b = {
            "type": "book",
            "event_id": "evt-1",
            "market_id": "mkt-x",
            "asset_id": "asset-x",
            "bids": [["0.41", "10"]],
            "asks": [["0.6", "5"]],
        }

        event_a = map_polymarket_payload(
            payload=payload_a, received_at=RECEIVED_AT, context=context
        )
        event_b = map_polymarket_payload(
            payload=payload_b, received_at=RECEIVED_AT, context=context
        )

        self.assertNotEqual(event_a.payload_hash, event_b.payload_hash)
        self.assertNotEqual(event_a.event_id, event_b.event_id)

    async def test_supports_event_type_alias_and_condition_id_fallback(self) -> None:
        context = MappingContext(adapter_version="test@1")
        payload = {
            "event_type": "new_market",
            "condition_id": "cond-77",
            "timestamp": "2026-04-14T11:00:00Z",
        }

        event = map_polymarket_payload(
            payload=payload, received_at=RECEIVED_AT, context=context
        )

        self.assertEqual(event.event_type, EventType.MARKET_CREATED)
        self.assertEqual(event.market_ref.market_id, "cond-77")

    async def test_parses_unix_seconds_and_milliseconds(self) -> None:
        context = MappingContext(adapter_version="test@1")
        sec_payload = {
            "type": "price_change",
            "market_id": "mkt-sec",
            "timestamp": 1_776_160_860,
        }
        ms_payload = {
            "type": "price_change",
            "market_id": "mkt-ms",
            "timestamp": 1_776_160_860_000,
        }

        sec_event = map_polymarket_payload(
            payload=sec_payload, received_at=RECEIVED_AT, context=context
        )
        ms_event = map_polymarket_payload(
            payload=ms_payload, received_at=RECEIVED_AT, context=context
        )

        self.assertEqual(sec_event.event_ts, ms_event.event_ts)

    async def test_raises_for_missing_or_unsupported_wire_type(self) -> None:
        context = MappingContext(adapter_version="test@1")
        missing_type_payload = {"market_id": "mkt-1"}
        unsupported_type_payload = {"type": "wallet_event", "market_id": "mkt-1"}

        with self.assertRaises(UnsupportedPayloadError):
            map_polymarket_payload(
                payload=missing_type_payload, received_at=RECEIVED_AT, context=context
            )
        with self.assertRaises(UnsupportedPayloadError):
            map_polymarket_payload(
                payload=unsupported_type_payload, received_at=RECEIVED_AT, context=context
            )

    async def test_raises_for_missing_market_identifier(self) -> None:
        context = MappingContext(adapter_version="test@1")
        payload = {"type": "book", "event_id": "evt-no-market"}

        with self.assertRaises(UnsupportedPayloadError):
            map_polymarket_payload(payload=payload, received_at=RECEIVED_AT, context=context)
