from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

from websockets.exceptions import InvalidStatus

from alarm_system.canonical_event import CanonicalEvent
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter
from alarm_system.ingestion.polymarket.supervisor import (
    PolymarketIngestionSupervisor,
    SupervisorConfig,
)

RECEIVED_AT = datetime(2026, 4, 14, 10, 10, tzinfo=timezone.utc)


def _book_payload(event_id: str) -> dict[str, Any]:
    return {
        "type": "book",
        "event_id": event_id,
        "market_id": "mkt-1",
        "asset_id": "asset-yes",
        "timestamp": "2026-04-14T10:00:00Z",
        "bids": [["0.45", "100"]],
        "asks": [["0.55", "80"]],
    }


class FakeWsClient:
    def __init__(
        self,
        sessions: list[list[dict[str, Any] | Exception]],
        connect_failures: list[BaseException] | None = None,
    ) -> None:
        self._sessions = sessions
        self._session_idx = -1
        self._messages: list[dict[str, Any] | Exception] = []
        self._connect_failures = list(connect_failures or [])
        self.connect_calls = 0
        self.close_calls = 0
        self.ping_sent = 0
        self.subscriptions: list[list[str]] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_failures:
            raise self._connect_failures.pop(0)
        self._session_idx += 1
        if self._session_idx >= len(self._sessions):
            self._messages = []
            return
        self._messages = list(self._sessions[self._session_idx])

    async def close(self) -> None:
        self.close_calls += 1

    async def subscribe_market(self, asset_ids: list[str]) -> None:
        self.subscriptions.append(asset_ids)

    async def send_ping(self) -> None:
        self.ping_sent += 1

    async def recv_json(self) -> dict[str, Any]:
        if self._messages:
            item = self._messages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        await asyncio.sleep(10)
        return {}


class PolymarketSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnects_and_resubscribes_after_transport_error(self) -> None:
        ws_client = FakeWsClient(
            sessions=[
                [OSError("transport dropped")],
                [{"type": "PONG"}, _book_payload("evt-1")],
            ]
        )
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)
            stop_event.set()

        await asyncio.wait_for(
            supervisor.run(on_events=on_events, stop_event=stop_event),
            timeout=1.0,
        )
        snapshot = metrics.snapshot()

        self.assertGreaterEqual(ws_client.connect_calls, 2)
        self.assertGreaterEqual(len(ws_client.subscriptions), 2)
        self.assertEqual(ws_client.subscriptions[0], ["asset-yes"])
        self.assertEqual(ws_client.subscriptions[1], ["asset-yes"])
        self.assertEqual(len(delivered), 1)
        self.assertIn("ingest_lag_ms", snapshot.timings_ms)
        self.assertIn(
            "ingest_lag_ms|event_type=orderbook_delta,source=polymarket",
            snapshot.series["timings_ms"],
        )
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.pong_seen_total"), 1)
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.emitted_batches_total"), 1)
        self.assertGreaterEqual(snapshot.counters.get("ingestion.supervisor.connected_total", 0), 2)
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.errors_total"), 1)
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.reconnect_total"), 1)

    async def test_heartbeat_timeout_increments_metrics(self) -> None:
        ws_client = FakeWsClient(sessions=[[]])
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

        async def on_events(events: list[CanonicalEvent]) -> None:
            return None

        task = asyncio.create_task(supervisor.run(on_events=on_events, stop_event=stop_event))
        await asyncio.sleep(0.15)
        stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        snapshot = metrics.snapshot()

        self.assertGreaterEqual(ws_client.ping_sent, 1)
        self.assertGreaterEqual(
            snapshot.counters.get("ingestion.supervisor.heartbeat_timeout_total", 0), 1
        )
        self.assertGreaterEqual(
            snapshot.counters.get("ingestion.supervisor.reconnect_total", 0), 1
        )

    async def test_bounded_dedup_cache_allows_event_after_eviction(self) -> None:
        ws_client = FakeWsClient(
            sessions=[
                [
                    {"type": "PONG"},
                    _book_payload("evt-1"),
                    _book_payload("evt-2"),
                    _book_payload("evt-1"),
                ]
            ]
        )
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                max_seen_event_ids=1,
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)
            if len(delivered) >= 3:
                stop_event.set()

        await asyncio.wait_for(
            supervisor.run(on_events=on_events, stop_event=stop_event),
            timeout=1.0,
        )
        snapshot = metrics.snapshot()

        self.assertEqual(
            [event.source_event_id for event in delivered],
            ["evt-1", "evt-2", "evt-1"],
        )
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.duplicate_suppressed_total", 0),
            0,
        )
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.emitted_batches_total"),
            3,
        )

    async def test_websocket_invalid_status_reconnects_not_fatal(self) -> None:
        response = MagicMock()
        response.status_code = 502
        ws_client = FakeWsClient(
            sessions=[[{"type": "PONG"}, _book_payload("evt-ok")]],
            connect_failures=[InvalidStatus(response)],
        )
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)
            stop_event.set()

        await asyncio.wait_for(
            supervisor.run(on_events=on_events, stop_event=stop_event),
            timeout=1.0,
        )
        snapshot = metrics.snapshot()

        self.assertEqual(len(delivered), 1)
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.errors_total"), 1)
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.fatal_errors_total", 0), 0
        )

    async def test_transport_oserror_triggers_reconnect_not_fatal(self) -> None:
        ws_client = FakeWsClient(
            sessions=[
                [OSError("connection reset")],
                [{"type": "PONG"}, _book_payload("evt-ok")],
            ]
        )
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()
        delivered: list[CanonicalEvent] = []

        async def on_events(events: list[CanonicalEvent]) -> None:
            delivered.extend(events)
            stop_event.set()

        await asyncio.wait_for(
            supervisor.run(on_events=on_events, stop_event=stop_event),
            timeout=1.0,
        )
        snapshot = metrics.snapshot()

        self.assertEqual(len(delivered), 1)
        self.assertEqual(snapshot.counters.get("ingestion.supervisor.errors_total"), 1)
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.fatal_errors_total", 0), 0
        )

    async def test_fatal_error_in_on_events_propagates_out_of_supervisor(self) -> None:
        ws_client = FakeWsClient(
            sessions=[[{"type": "PONG"}, _book_payload("evt-fatal")]]
        )
        metrics = InMemoryMetrics()
        supervisor = PolymarketIngestionSupervisor(
            ws_client=ws_client,  # type: ignore[arg-type]
            adapter=PolymarketMarketAdapter(metrics=metrics),
            config=SupervisorConfig(
                asset_ids=["asset-yes"],
                reconnect_backoff_sec=0.0,
                receive_timeout_sec=0.01,
            ),
            metrics=metrics,
        )
        stop_event = asyncio.Event()

        async def on_events(events: list[CanonicalEvent]) -> None:
            raise AssertionError("downstream exploded")

        with self.assertRaises(AssertionError, msg="downstream exploded"):
            await asyncio.wait_for(
                supervisor.run(on_events=on_events, stop_event=stop_event),
                timeout=1.0,
            )
        snapshot = metrics.snapshot()

        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.fatal_errors_total"), 1
        )
        self.assertEqual(
            snapshot.counters.get("ingestion.supervisor.errors_total", 0), 0
        )
