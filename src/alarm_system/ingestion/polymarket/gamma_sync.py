from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from alarm_system.canonical_event import (
    CanonicalEvent,
    EventType,
    MarketRef,
    Source,
    TraceContext,
    build_event_id,
    build_payload_hash,
)
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.validation import validate_canonical_event


class GammaClient(Protocol):
    async def fetch_markets(self, tag_ids: list[int], limit: int) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class GammaSyncConfig:
    base_url: str = "https://gamma-api.polymarket.com"
    limit: int = 100
    adapter_version: str = "polymarket-gamma@v1"


class HttpGammaClient:
    def __init__(self, config: GammaSyncConfig | None = None) -> None:
        self._config = config or GammaSyncConfig()

    async def fetch_markets(self, tag_ids: list[int], limit: int) -> list[dict[str, Any]]:
        query = [("closed", "false"), ("limit", str(limit))]
        for tag_id in tag_ids:
            query.append(("tag_id", str(tag_id)))
        url = f"{self._config.base_url}/markets?{urlencode(query)}"
        raw_payload = await asyncio.to_thread(self._read_url, url)
        parsed = json.loads(raw_payload)
        if not isinstance(parsed, list):
            raise ValueError("Gamma API response must be an array")
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _read_url(url: str) -> str:
        with urlopen(url, timeout=20) as response:  # nosec: B310 (trusted static host)
            return response.read().decode("utf-8")


class GammaMetadataSyncWorker:
    def __init__(
        self,
        client: GammaClient | None = None,
        config: GammaSyncConfig | None = None,
        metrics: InMemoryMetrics | None = None,
    ) -> None:
        self._config = config or GammaSyncConfig()
        self._client = client or HttpGammaClient(self._config)
        self._metrics = metrics or InMemoryMetrics()

    async def poll_once(self, tag_ids: list[int]) -> list[CanonicalEvent]:
        started = datetime.now(timezone.utc)
        try:
            markets = await self._client.fetch_markets(
                tag_ids=tag_ids,
                limit=self._config.limit,
            )
        except Exception:
            self._metrics.increment("ingestion.gamma.poll_errors_total")
            raise
        now = datetime.now(timezone.utc)
        events: list[CanonicalEvent] = []
        for market in markets:
            market_id = self._extract_market_id(market)
            if market_id is None:
                continue
            source_event_id = f"gamma:{market_id}:{now.isoformat()}"
            payload_hash = build_payload_hash(market)
            event = CanonicalEvent(
                event_id=build_event_id(
                    event_type=EventType.METADATA_REFRESH,
                    market_id=market_id,
                    source_event_id=source_event_id,
                    payload_hash=payload_hash,
                ),
                source=Source.POLYMARKET,
                source_event_id=source_event_id,
                event_type=EventType.METADATA_REFRESH,
                market_ref=MarketRef(market_id=market_id),
                event_ts=now,
                ingested_ts=now,
                payload=market,
                payload_hash=payload_hash,
                trace=TraceContext(
                    correlation_id=source_event_id,
                    partition_key=market_id,
                    producer="polymarket_gamma_sync",
                    adapter_version=self._config.adapter_version,
                ),
            )
            validate_canonical_event(event)
            events.append(event)

        elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        self._metrics.increment("ingestion.gamma.poll_total")
        self._metrics.set_gauge("ingestion.gamma.last_market_count", float(len(events)))
        self._metrics.observe_timing_ms("ingestion.gamma.poll_latency_ms", elapsed_ms)
        return events

    @staticmethod
    def _extract_market_id(payload: dict[str, Any]) -> str | None:
        for key in ("conditionId", "condition_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None
