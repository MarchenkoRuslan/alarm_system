from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from alarm_system.canonical_event import CanonicalEvent
from alarm_system.registry import EnumRegistry


class MarketSource(str, Enum):
    """
    Supported market adapter sources.

    NOTE:
    - `POLYMARKET` is the only production-enabled source for MVP.
    - Additional sources can be added without changing rule/delivery layers.
    """

    POLYMARKET = "polymarket"


@dataclass(frozen=True)
class AdapterEnvelope:
    """Raw source payload with metadata used by adapter implementations."""

    source: MarketSource
    payload: dict[str, Any]
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketAdapter(ABC):
    """
    Source adapter boundary.

    Adding a new market source should only require:
    1) adding a `MarketSource` enum value,
    2) implementing this interface,
    3) registering the adapter in `AdapterRegistry`,
    4) adding contract fixtures/tests.
    """

    @property
    @abstractmethod
    def source(self) -> MarketSource:
        ...

    @abstractmethod
    async def normalize(self, envelope: AdapterEnvelope) -> list[CanonicalEvent]:
        """
        Convert source-native payload into canonical events.
        Must be deterministic and idempotent for replay safety.
        """
        ...


class AdapterRegistry:
    """Simple runtime registry of source adapters."""

    def __init__(self) -> None:
        self._registry: EnumRegistry[MarketSource, MarketAdapter] = (
            EnumRegistry()
        )

    def register(self, adapter: MarketAdapter) -> None:
        self._registry.register(adapter.source, adapter)

    def get(self, source: MarketSource) -> MarketAdapter:
        try:
            return self._registry.get(source)
        except KeyError:
            raise KeyError(
                f"No market adapter registered for source '{source}'. "
                f"Available: {self._registry.keys()}"
            )

    def registered_sources(self) -> list[MarketSource]:
        return self._registry.keys()
