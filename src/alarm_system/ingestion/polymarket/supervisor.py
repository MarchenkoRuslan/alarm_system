from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque
from typing import Awaitable, Callable

from alarm_system.adapters import AdapterEnvelope, MarketSource
from alarm_system.canonical_event import CanonicalEvent
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter
from alarm_system.ingestion.polymarket.ws_client import PolymarketWsClient

EventBatchHandler = Callable[[list[CanonicalEvent]], Awaitable[None]]


class HeartbeatTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupervisorConfig:
    asset_ids: list[str]
    ping_interval_sec: float = 10.0
    pong_timeout_sec: float = 20.0
    reconnect_backoff_sec: float = 2.0
    receive_timeout_sec: float = 1.0
    max_seen_event_ids: int = 50_000


class PolymarketIngestionSupervisor:
    def __init__(
        self,
        ws_client: PolymarketWsClient,
        adapter: PolymarketMarketAdapter,
        config: SupervisorConfig,
        metrics: InMemoryMetrics | None = None,
    ) -> None:
        self._ws_client = ws_client
        self._adapter = adapter
        self._config = config
        self._metrics = metrics or InMemoryMetrics()
        self._seen_event_ids: set[str] = set()
        self._seen_event_ids_order: deque[str] = deque()

    async def run(self, on_events: EventBatchHandler, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._run_connected(on_events=on_events, stop_event=stop_event)
            except HeartbeatTimeoutError:
                self._metrics.increment("ingestion.supervisor.reconnect_total")
            except (OSError, ConnectionError, ValueError):
                self._metrics.increment("ingestion.supervisor.errors_total")
                self._metrics.increment("ingestion.supervisor.reconnect_total")
            except Exception:
                self._metrics.increment("ingestion.supervisor.fatal_errors_total")
                raise
            finally:
                await self._ws_client.close()
                if not stop_event.is_set():
                    await asyncio.sleep(self._config.reconnect_backoff_sec)

    async def _run_connected(self, on_events: EventBatchHandler, stop_event: asyncio.Event) -> None:
        await self._ws_client.connect()
        await self._ws_client.subscribe_market(self._config.asset_ids)

        now_monotonic = asyncio.get_running_loop().time()
        last_ping_sent = now_monotonic
        last_pong_seen = now_monotonic
        self._metrics.increment("ingestion.supervisor.connected_total")

        while not stop_event.is_set():
            current = asyncio.get_running_loop().time()

            if current - last_ping_sent >= self._config.ping_interval_sec:
                await self._ws_client.send_ping()
                last_ping_sent = current
                self._metrics.increment("ingestion.supervisor.ping_sent_total")

            if current - last_pong_seen > self._config.pong_timeout_sec:
                self._metrics.increment("ingestion.supervisor.heartbeat_timeout_total")
                raise HeartbeatTimeoutError("No PONG received within timeout")

            try:
                message = await asyncio.wait_for(
                    self._ws_client.recv_json(),
                    timeout=self._config.receive_timeout_sec,
                )
            except asyncio.TimeoutError:
                continue

            message_type = message.get("type") or message.get("event_type")
            if message_type == "PONG":
                last_pong_seen = asyncio.get_running_loop().time()
                self._metrics.increment("ingestion.supervisor.pong_seen_total")
                continue

            received_at = datetime.now(timezone.utc)
            envelope = AdapterEnvelope(
                source=MarketSource.POLYMARKET,
                payload=message,
                received_at=received_at,
            )
            normalized = await self._adapter.normalize(envelope)
            filtered: list[CanonicalEvent] = []
            for event in normalized:
                ingest_lag_ms = max(
                    0.0,
                    (
                        event.ingested_ts.astimezone(timezone.utc)
                        - event.event_ts.astimezone(timezone.utc)
                    ).total_seconds()
                    * 1000.0,
                )
                self._metrics.observe_timing_ms(
                    "ingest_lag_ms",
                    ingest_lag_ms,
                    labels={
                        "source": event.source.value,
                        "event_type": event.event_type.value,
                    },
                )
                dedup_key = event.event_id
                if dedup_key in self._seen_event_ids:
                    self._metrics.increment(
                        "ingestion.supervisor.duplicate_suppressed_total"
                    )
                    continue
                self._remember_event_id(dedup_key)
                filtered.append(event)

            if filtered:
                await on_events(filtered)
                self._metrics.increment(
                    "ingestion.supervisor.emitted_batches_total"
                )

    def _remember_event_id(self, event_id: str) -> None:
        self._seen_event_ids.add(event_id)
        self._seen_event_ids_order.append(event_id)
        max_size = self._config.max_seen_event_ids
        while len(self._seen_event_ids_order) > max_size:
            expired = self._seen_event_ids_order.popleft()
            self._seen_event_ids.discard(expired)
