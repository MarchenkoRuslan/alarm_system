from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from alarm_system.canonical_event import (
    CanonicalEvent,
    EventType,
    MarketRef,
    Source,
    TraceContext,
    build_payload_hash,
    ensure_utc,
)
from alarm_system.ingestion.polymarket.event_id import build_canonical_event_id

EVENT_TYPE_MAP: dict[str, EventType] = {
    "book": EventType.ORDERBOOK_DELTA,
    "price_change": EventType.MARKET_SNAPSHOT,
    "last_trade_price": EventType.TRADE,
    "new_market": EventType.MARKET_CREATED,
    "market_resolved": EventType.MARKET_RESOLVED,
}

TIMESTAMP_KEYS = (
    "timestamp",
    "ts",
    "event_ts",
    "time",
    "created_at",
    "updated_at",
)


class UnsupportedPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class MappingContext:
    adapter_version: str
    producer: str = "polymarket_ws"


def detect_wire_event_type(payload: dict[str, Any]) -> str:
    for key in ("event_type", "event", "type"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise UnsupportedPayloadError("Missing wire event type in payload")


def _parse_timestamp(value: Any, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return fallback
        return ensure_utc(parsed)
    return fallback


def _extract_market_ref(payload: dict[str, Any]) -> MarketRef:
    market_id = payload.get("market_id") or payload.get("market") or payload.get("condition_id")
    if not isinstance(market_id, str) or not market_id:
        raise UnsupportedPayloadError("Unable to resolve market_id from payload")
    outcome_id = payload.get("outcome_id") or payload.get("asset_id")
    event_id = payload.get("event_id")
    return MarketRef(
        market_id=market_id,
        event_id=event_id if isinstance(event_id, str) else None,
        outcome_id=outcome_id if isinstance(outcome_id, str) else None,
    )


def _source_event_id(payload: dict[str, Any], payload_hash: str) -> str:
    for key in ("source_event_id", "event_id", "id", "message_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    fallback_fields = (
        str(payload.get("market_id") or payload.get("condition_id") or "unknown"),
        str(payload.get("type") or payload.get("event_type") or "unknown"),
        str(payload.get("timestamp") or payload.get("ts") or payload_hash[:16]),
    )
    return ":".join(fallback_fields)


def map_polymarket_payload(
    payload: dict[str, Any],
    received_at: datetime,
    context: MappingContext,
) -> CanonicalEvent:
    wire_event_type = detect_wire_event_type(payload)
    canonical_event_type = EVENT_TYPE_MAP.get(wire_event_type)
    if canonical_event_type is None:
        raise UnsupportedPayloadError(f"Unsupported wire event type: {wire_event_type}")

    market_ref = _extract_market_ref(payload)
    payload_hash = build_payload_hash(payload)
    source_event_id = _source_event_id(payload, payload_hash=payload_hash)

    event_ts = received_at
    for key in TIMESTAMP_KEYS:
        if key in payload:
            event_ts = _parse_timestamp(payload.get(key), fallback=received_at)
            break

    trace = TraceContext(
        correlation_id=source_event_id,
        partition_key=market_ref.market_id,
        producer=context.producer,
        adapter_version=context.adapter_version,
    )
    return CanonicalEvent(
        event_id=build_canonical_event_id(
            event_type=canonical_event_type,
            market_id=market_ref.market_id,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=canonical_event_type,
        market_ref=market_ref,
        event_ts=ensure_utc(event_ts),
        ingested_ts=ensure_utc(received_at),
        payload=payload,
        payload_hash=payload_hash,
        trace=trace,
    )
