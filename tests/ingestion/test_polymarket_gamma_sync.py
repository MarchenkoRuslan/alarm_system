from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from alarm_system.canonical_event import EventType
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.event_id import build_canonical_event_id
from alarm_system.ingestion.polymarket.gamma_sync import GammaMetadataSyncWorker


class FakeGammaClient:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload
        self.calls: list[tuple[list[int], int]] = []

    async def fetch_markets(self, tag_ids: list[int], limit: int) -> list[dict[str, Any]]:
        self.calls.append((tag_ids, limit))
        return list(self._payload)


class PolymarketGammaSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_once_maps_markets_to_metadata_refresh_events(self) -> None:
        fake_client = FakeGammaClient(
            payload=[
                {"conditionId": "cond-1", "question": "Q1"},
                {"condition_id": "cond-2", "question": "Q2"},
                {"id": "cond-3", "question": "Q3"},
            ]
        )
        metrics = InMemoryMetrics()
        worker = GammaMetadataSyncWorker(client=fake_client, metrics=metrics)

        events = await worker.poll_once(tag_ids=[10, 20])
        snapshot = metrics.snapshot()

        self.assertEqual(len(events), 3)
        self.assertTrue(all(event.event_type == EventType.METADATA_REFRESH for event in events))
        self.assertEqual(events[0].market_ref.market_id, "cond-1")
        self.assertEqual(events[1].market_ref.market_id, "cond-2")
        self.assertEqual(events[2].market_ref.market_id, "cond-3")
        expected_event_id = build_canonical_event_id(
            event_type=EventType.METADATA_REFRESH,
            market_id=events[0].market_ref.market_id,
            source_event_id=events[0].source_event_id or "",
            payload_hash=events[0].payload_hash,
        )
        self.assertEqual(events[0].event_id, expected_event_id)
        self.assertNotEqual(events[0].event_id, events[0].payload_hash[:32])
        self.assertEqual(fake_client.calls[0][0], [10, 20])
        self.assertEqual(snapshot.counters.get("ingestion.gamma.poll_total"), 1)
        self.assertIn("ingestion.gamma.poll_latency_ms", snapshot.timings_ms)
        self.assertEqual(snapshot.gauges.get("ingestion.gamma.last_market_count"), 3.0)

    async def test_source_event_id_is_unique_across_polls_within_same_second(self) -> None:
        market_payload = {"conditionId": "cond-x", "question": "Stable"}
        t_sec1 = datetime(2026, 4, 14, 10, 0, 0, 0, tzinfo=timezone.utc)
        t_sec2 = datetime(2026, 4, 14, 10, 0, 0, 500_000, tzinfo=timezone.utc)

        fake_client = FakeGammaClient(payload=[market_payload])
        worker = GammaMetadataSyncWorker(client=fake_client)

        with patch(
            "alarm_system.ingestion.polymarket.gamma_sync.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = t_sec1
            events_first = await worker.poll_once(tag_ids=[])

            mock_dt.now.return_value = t_sec2
            events_second = await worker.poll_once(tag_ids=[])

        self.assertEqual(len(events_first), 1)
        self.assertEqual(len(events_second), 1)
        self.assertNotEqual(
            events_first[0].source_event_id,
            events_second[0].source_event_id,
        )
        self.assertNotEqual(
            events_first[0].event_id,
            events_second[0].event_id,
        )

    async def test_poll_once_skips_payload_without_market_identifier(self) -> None:
        fake_client = FakeGammaClient(
            payload=[
                {"conditionId": "cond-1", "question": "ok"},
                {"question": "skip-me"},
            ]
        )
        worker = GammaMetadataSyncWorker(client=fake_client)

        events = await worker.poll_once(tag_ids=[])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].market_ref.market_id, "cond-1")
