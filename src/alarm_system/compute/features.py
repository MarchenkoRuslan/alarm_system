from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alarm_system.canonical_event import CanonicalEvent
from alarm_system.normalization import extract_event_tags, to_float


@dataclass(frozen=True)
class FeatureSnapshot:
    values: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def _compute_spread_bps(payload: dict[str, Any]) -> float | None:
    bids = payload.get("bids")
    asks = payload.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        return None
    best_bid = to_float(bids[0][0]) if isinstance(bids[0], list) and bids[0] else None
    best_ask = to_float(asks[0][0]) if isinstance(asks[0], list) and asks[0] else None
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None
    return ((best_ask - best_bid) / mid) * 10_000.0


def _compute_book_imbalance(payload: dict[str, Any], top_n: int = 3) -> float | None:
    bids = payload.get("bids")
    asks = payload.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None

    def _sum_levels(levels: list[Any]) -> float:
        total = 0.0
        for level in levels[:top_n]:
            if not isinstance(level, list) or len(level) < 2:
                continue
            qty = to_float(level[1])
            if qty is None:
                continue
            total += qty
        return total

    bid_depth = _sum_levels(bids)
    ask_depth = _sum_levels(asks)
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return (bid_depth - ask_depth) / total


def _extract_position_signals(payload: dict[str, Any]) -> dict[str, float]:
    action = payload.get("action")
    if not isinstance(action, str):
        return {}
    normalized = action.strip().lower()
    mapping = {
        "open": "PositionOpened",
        "close": "PositionClosed",
        "increase": "PositionIncreased",
        "decrease": "PositionDecreased",
    }
    signal_name = mapping.get(normalized)
    if signal_name is None:
        return {}
    return {signal_name: 1.0}


def extract_feature_snapshot(event: CanonicalEvent) -> FeatureSnapshot:
    payload = event.payload
    values: dict[str, float] = {}

    direct_numeric_fields = {
        "price_return_1m_pct": ("price_return_1m_pct", "delta_1m_pct"),
        "price_return_5m_pct": ("price_return_5m_pct", "delta_5m_pct"),
        "liquidity_usd": ("liquidity_usd", "liquidity", "liquidityNum"),
        "volume_5m": ("volume_5m",),
        "smart_score": ("smart_score", "smartScore"),
        "account_age_days": ("account_age_days", "accountAgeDays"),
    }
    for feature_name, aliases in direct_numeric_fields.items():
        for alias in aliases:
            value = to_float(payload.get(alias))
            if value is not None:
                values[feature_name] = value
                break

    delta = to_float(payload.get("delta"))
    if "price_return_1m_pct" not in values and delta is not None:
        values["price_return_1m_pct"] = delta * 100.0

    spread_bps = _compute_spread_bps(payload)
    if spread_bps is not None:
        values["spread_bps"] = spread_bps

    book_imbalance = _compute_book_imbalance(payload)
    if book_imbalance is not None:
        values["book_imbalance_topN"] = book_imbalance

    values.update(_extract_position_signals(payload))
    # Preserve scalar payload fields for field-aware rule matching/evaluation.
    for key, raw_value in payload.items():
        if key in values:
            continue
        if isinstance(raw_value, (str, int, float, bool)):
            values[key] = raw_value
    return FeatureSnapshot(values=values, tags=extract_event_tags(payload))
