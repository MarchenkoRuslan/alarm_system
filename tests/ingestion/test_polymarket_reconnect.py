from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any

from alarm_system.canonical_event import CanonicalEvent
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter
from alarm_system.ingestion.polymarket.supervisor import (
    PolymarketIngestionSupervisor,
    SupervisorConfig,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket"


def _fixture(name: str) -> dict[str, Any]:
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class FakeWsClient:
    def __init__(self, sessions: list[list[object]]) -> None:
        self._sessions = sessions
        self._session_idx = -1
        self._current_messages: list[object] = []
        self.connect_calls = 0
        self.subscriptions: list[list[str]] = []
        self.closed = False

    async def connect(self) -> None:
        self.connect_calls += 1
        self._session_idx += 1
        if self._session_idx >= len(self._sessions):
            self._current_messages = []
            return
        self._current_messages = list(self._sessions[self._session_idx])

    async def close(self) -> None:
        self.closed = True

    async def subscribe_market(self, asset_ids: list[str]) -> None:
        self.subscriptions.append(asset_ids)

    async def send_ping(self) -> None:
        return None

    async def recv_json(self) -> dict[str, Any]:
        if self._current_messages:
            item = self._current_messages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item  # type: ignore[return-value]
        await asyncio.sleep(10)
        return {}


def _book_payload(event_id: str, timestamp: str) -> dict[str, Any]:
    payload = _fixture("book.json")
    payload["event_id"] = event_id
    payload["timestamp"] = timestamp
    return payload


class PolymarketReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_storm_does_not_emit_duplicate_events(self) -> None:
        duplicated_payload = _fixture("book.json")
        unsupported_payload = {
            "type": "book",
            "event_id": "evt-unsupported-no-market",
            "timestamp": "2026-04-14T10:00:00Z",
        }
        sessions = [
            [unsupported_payload],
            [
                {"type": "PONG"},
                duplicated_payload,
                duplicated_payload,
                duplicated_payload,
            ],
        ]
        ws_client = FakeWsClient(sessions=sessions)
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                ping_interval_sec=0.01,
                pong_timeout_sec=0.03,
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)

        task = asyncio.create_task(supervisor.run(on_events=on_events, stop_event=stop_event))
        await asyncio.sleep(0.3)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        snapshot = metrics.snapshot()

        self.assertGreaterEqual(ws_client.connect_calls, 2)
        self.assertTrue(ws_client.closed)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].source_event_id, "evt-book-1")
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.emitted_batches_total"),
            1,
        )
        self.assertGreaterEqual(
            snapshot.counters.get("ingestion.supervisor.connected_total", 0),
            2,
        )

    async def test_reconnect_storm_with_partial_replay_keeps_unique_emits(self) -> None:
        sessions: list[list[object]] = [
            [
                {"type": "PONG"},
                _book_payload("evt-storm-1", "2026-04-14T10:00:00Z"),
                _book_payload("evt-storm-2", "2026-04-14T10:00:01Z"),
                _book_payload("evt-storm-3", "2026-04-14T10:00:02Z"),
                ConnectionError("drop-1"),
            ],
            [
                {"type": "PONG"},
                _book_payload("evt-storm-3", "2026-04-14T10:00:02Z"),  # replay ~10%
                _book_payload("evt-storm-4", "2026-04-14T10:00:03Z"),
                _book_payload("evt-storm-5", "2026-04-14T10:00:04Z"),
                _book_payload("evt-storm-6", "2026-04-14T10:00:05Z"),
                ConnectionError("drop-2"),
            ],
            [
                {"type": "PONG"},
                _book_payload("evt-storm-6", "2026-04-14T10:00:05Z"),  # replay ~10%
                _book_payload("evt-storm-7", "2026-04-14T10:00:06Z"),
                _book_payload("evt-storm-8", "2026-04-14T10:00:07Z"),
                _book_payload("evt-storm-9", "2026-04-14T10:00:08Z"),
                ConnectionError("drop-3"),
            ],
            [
                {"type": "PONG"},
                _book_payload("evt-storm-9", "2026-04-14T10:00:08Z"),  # replay ~10%
                _book_payload("evt-storm-10", "2026-04-14T10:00:09Z"),
            ],
        ]
        ws_client = FakeWsClient(sessions=sessions)
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                ping_interval_sec=0.01,
                pong_timeout_sec=0.03,
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)

        task = asyncio.create_task(
            supervisor.run(on_events=on_events, stop_event=stop_event)
        )
        await asyncio.sleep(0.35)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        snapshot = metrics.snapshot()

        self.assertGreaterEqual(ws_client.connect_calls, 4)
        self.assertGreaterEqual(
            snapshot.counters.get("ingestion.supervisor.reconnect_total", 0),
            3,
        )
        self.assertGreaterEqual(
            snapshot.counters.get(
                "ingestion.supervisor.duplicate_suppressed_total", 0
            ),
            3,
        )
        source_ids = {event.source_event_id for event in delivered}
        self.assertEqual(len(source_ids), 10)
        self.assertEqual(len(delivered), 10)
