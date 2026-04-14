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
    POLYMARKET_ONCHAIN = "polymarket_onchain"


class EventType(str, Enum):
    MARKET_SNAPSHOT = "market_snapshot"
    ORDERBOOK_DELTA = "orderbook_delta"
    TRADE = "trade"
    POSITION_UPDATE = "position_update"
    LIQUIDITY_UPDATE = "liquidity_update"
    MARKET_CREATED = "market_created"
    MARKET_RESOLVED = "market_resolved"
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
    # Source-side timestamp used as the SLO start point.
    event_ts: datetime
    ingested_ts: datetime
    payload: dict[str, Any]
    payload_hash: str
    trace: TraceContext


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(serialized).hexdigest()


def build_event_id(
    event_type: EventType,
    market_id: str,
    source_event_id: str,
    payload_hash: str,
) -> str:
    """Build deterministic canonical event identifier from stable tuple fields.

    Contract: sha256(event_type | market_id | source_event_id | payload_hash)[:32]
    """
    digest = sha256(
        f"{event_type.value}|{market_id}|{source_event_id}|{payload_hash}".encode("utf-8")
    ).hexdigest()
    return digest[:32]


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
    normalized_event_ts = ensure_utc(event_ts) if event_ts else now
    return CanonicalEvent(
        event_id=str(uuid4()),
        source=source,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=market_ref,
        entity_ref=entity_ref,
        event_ts=normalized_event_ts,
        ingested_ts=now,
        payload=payload,
        payload_hash=build_payload_hash(payload),
        trace=trace,
    )
