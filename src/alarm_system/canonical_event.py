from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Source(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    POLYMARKET_ONCHAIN = "polymarket_onchain"


class EventType(str, Enum):
    MARKET_SNAPSHOT = "market_snapshot"
    ORDERBOOK_DELTA = "orderbook_delta"
    TRADE = "trade"
    TICKER = "ticker"
    MARKET_LIFECYCLE = "market_lifecycle"
    WALLET_ACTIVITY = "wallet_activity"
    METADATA_REFRESH = "metadata_refresh"


class MarketRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    event_id: str | None = None
    outcome_id: str | None = None


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_address: str | None = None
    entity_id: str | None = None
    label: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    partition_key: str
    producer: str | None = None
    adapter_version: str | None = None


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    event_id: str
    source: Source
    source_event_id: str | None = None
    event_type: EventType
    market_ref: MarketRef
    entity_ref: EntityRef | None = None
    event_ts: datetime
    ingested_ts: datetime
    payload: dict[str, Any]
    payload_hash: str
    trace: TraceContext


def build_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(serialized).hexdigest()


def make_event(
    source: Source,
    event_type: EventType,
    market_ref: MarketRef,
    trace: TraceContext,
    payload: dict[str, Any],
    source_event_id: str | None = None,
    entity_ref: EntityRef | None = None,
    event_ts: datetime | None = None,
) -> CanonicalEvent:
    now = datetime.now(timezone.utc)
    return CanonicalEvent(
        event_id=str(uuid4()),
        source=source,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=market_ref,
        entity_ref=entity_ref,
        event_ts=event_ts or now,
        ingested_ts=now,
        payload=payload,
        payload_hash=build_payload_hash(payload),
        trace=trace,
    )
