from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alarm_system.adapters import AdapterEnvelope, MarketSource
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter

RECEIVED_AT = datetime(2026, 4, 14, 10, 10, tzinfo=timezone.utc)


class PolymarketAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_raises_for_non_polymarket_source(self) -> None:
        adapter = PolymarketMarketAdapter()
        envelope = AdapterEnvelope(
            source=MarketSource.POLYMARKET, payload={"type": "book", "market_id": "mkt-1"}
        )
        # Explicitly pass invalid enum instance by type ignore to validate guard.
        envelope = AdapterEnvelope(  # type: ignore[assignment]
            source="other", payload=envelope.payload, received_at=RECEIVED_AT
        )

        with self.assertRaises(ValueError):
            await adapter.normalize(envelope)  # type: ignore[arg-type]

    async def test_returns_empty_for_unsupported_payload_and_tracks_metric(self) -> None:
        metrics = InMemoryMetrics()
        adapter = PolymarketMarketAdapter(metrics=metrics)
        envelope = AdapterEnvelope(
            source=MarketSource.POLYMARKET,
            payload={"type": "unknown_event", "market_id": "mkt-1"},
            received_at=RECEIVED_AT,
        )

        result = await adapter.normalize(envelope)
        snapshot = metrics.snapshot()

        self.assertEqual(result, [])
        self.assertEqual(snapshot.counters.get("ingestion.normalize.unsupported_total"), 1)
        self.assertIn("ingestion.normalize.latency_ms", snapshot.timings_ms)

    async def test_success_path_tracks_success_metric(self) -> None:
        metrics = InMemoryMetrics()
        adapter = PolymarketMarketAdapter(metrics=metrics)
        envelope = AdapterEnvelope(
            source=MarketSource.POLYMARKET,
            payload={
                "type": "book",
                "event_id": "evt-ok-1",
                "market_id": "mkt-1",
                "asset_id": "asset-1",
                "timestamp": "2026-04-14T10:00:00Z",
            },
            received_at=RECEIVED_AT,
        )

        result = await adapter.normalize(envelope)
        snapshot = metrics.snapshot()

        self.assertEqual(len(result), 1)
        self.assertEqual(snapshot.counters.get("ingestion.normalize.success_total"), 1)
        self.assertIn("ingestion.normalize.latency_ms", snapshot.timings_ms)
