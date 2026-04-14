from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from alarm_system.adapters import AdapterEnvelope, MarketAdapter, MarketSource
from alarm_system.canonical_event import CanonicalEvent
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.mapper import (
    MappingContext,
    UnsupportedPayloadError,
    map_polymarket_payload,
)
from alarm_system.ingestion.validation import validate_canonical_event


@dataclass(frozen=True)
class PolymarketAdapterConfig:
    adapter_version: str = "polymarket-ws@phase1"


class PolymarketMarketAdapter(MarketAdapter):
    def __init__(
        self,
        config: PolymarketAdapterConfig | None = None,
        metrics: InMemoryMetrics | None = None,
    ) -> None:
        self._config = config or PolymarketAdapterConfig()
        self._metrics = metrics or InMemoryMetrics()
        self._mapping_context = MappingContext(adapter_version=self._config.adapter_version)

    @property
    def source(self) -> MarketSource:
        return MarketSource.POLYMARKET

    async def normalize(self, envelope: AdapterEnvelope) -> list[CanonicalEvent]:
        if envelope.source is not MarketSource.POLYMARKET:
            raise ValueError(
                f"Polymarket adapter can normalize only '{MarketSource.POLYMARKET.value}' envelopes"
            )
        started_at = datetime.now(timezone.utc)
        try:
            event = map_polymarket_payload(
                payload=envelope.payload,
                received_at=envelope.received_at,
                context=self._mapping_context,
            )
            validate_canonical_event(event)
            self._metrics.increment("ingestion.normalize.success_total")
            return [event]
        except UnsupportedPayloadError:
            self._metrics.increment("ingestion.normalize.unsupported_total")
            return []
        finally:
            elapsed_ms = (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
            self._metrics.observe_timing_ms("ingestion.normalize.latency_ms", elapsed_ms)
