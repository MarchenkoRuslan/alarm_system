"""Alert-level ``filters_json``: validation and runtime evaluation.

Per-user thresholds are AND-ed on top of the server ``AlertRuleV1`` DSL.
Missing numeric signals fail the filter (conservative).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alarm_system.normalization import normalize_tag, to_float
from alarm_system.rules_dsl import AlertRuleV1, RuleFilters, RuleType


_NUMERIC_ALERT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("return_1m_pct_min", "price_return_1m_pct", ">="),
    ("return_5m_pct_min", "price_return_5m_pct", ">="),
    ("spread_bps_max", "spread_bps", "<="),
    ("imbalance_abs_min", "book_imbalance_topN", "abs_gte"),
    ("liquidity_usd_min", "liquidity_usd", ">="),
)


def _passes_category_tags(
    filters_json: dict[str, str | int | float | bool | list[str]],
    event_tags: set[str],
) -> bool:
    raw_tags = filters_json.get("category_tags")
    if raw_tags is None or raw_tags == []:
        return True
    if not isinstance(raw_tags, list):
        return False
    user_tags = {
        normalize_tag(x)
        for x in raw_tags
        if isinstance(x, str) and x.strip()
    }
    if not user_tags:
        return True
    return bool(user_tags & event_tags)


def _compare_signal_threshold(
    observed: float,
    op: str,
    threshold: float,
) -> bool:
    if op == ">=":
        return observed >= threshold
    if op == "<=":
        return observed <= threshold
    if op == "abs_gte":
        return abs(observed) >= threshold
    return False


def _passes_numeric_alert_filters(
    filters_json: dict[str, str | int | float | bool | list[str]],
    signal_values: dict[str, Any],
) -> bool:
    for json_key, signal, op in _NUMERIC_ALERT_CHECKS:
        if json_key not in filters_json:
            continue
        v = to_float(filters_json[json_key])
        if v is None:
            return False
        observed = signal_values.get(signal)
        if observed is None:
            return False
        observed_num = to_float(observed)
        if observed_num is None:
            return False
        if not _compare_signal_threshold(observed_num, op, v):
            return False
    return True


def passes_alert_filters(
    filters_json: dict[str, str | int | float | bool | list[str]],
    *,
    signal_values: dict[str, Any],
    event_tags: set[str],
) -> bool:
    """Return True if alert-level filters pass for this event snapshot."""

    if not filters_json:
        return True
    if not _passes_category_tags(filters_json, event_tags):
        return False
    return _passes_numeric_alert_filters(filters_json, signal_values)


def effective_require_event_tag(
    rule_filters: RuleFilters,
    alert_fj: dict[str, Any],
) -> str | None:
    """Tag that must appear on the event: alert ``filters_json`` overrides rule."""

    u = alert_fj.get("require_event_tag")
    if isinstance(u, str) and u.strip():
        return normalize_tag(u)
    r = rule_filters.require_event_tag
    if isinstance(r, str) and r.strip():
        return normalize_tag(r)
    return None


def effective_min_smart_score(
    rule_filters: RuleFilters,
    alert_fj: dict[str, Any],
) -> float | None:
    """At least the server floor; user can only tighten (higher min)."""

    r = rule_filters.min_smart_score
    u = to_float(alert_fj.get("min_smart_score"))
    if r is not None and u is not None:
        return max(r, u)
    if u is not None:
        return u
    return r


def matched_filter_evidence(
    rule: AlertRuleV1,
    filters_json: dict[str, str | int | float | bool | list[str]],
    *,
    rule_tags: set[str],
    event_tags: set[str],
    signal_values: dict[str, Any],
) -> dict[str, str]:
    """Human-readable entries for ``TriggerReason.matched_filters`` after gates pass."""

    matched: dict[str, str] = {}
    filters = rule.filters
    fj = dict(filters_json) if filters_json else {}

    if filters.category_tags and event_tags:
        overlap = sorted(rule_tags.intersection(event_tags))
        if overlap:
            matched["category_tags"] = ",".join(overlap)

    req_tag = effective_require_event_tag(filters, fj)
    if req_tag is not None:
        matched["require_event_tag"] = req_tag

    min_smart = effective_min_smart_score(filters, fj)
    if min_smart is not None:
        obs = signal_values.get("smart_score")
        obs_part = "missing" if obs is None else f"{float(obs):g}"
        matched["min_smart_score"] = (
            f"threshold={min_smart:g},observed={obs_part}"
        )

    min_age = effective_min_account_age_days(filters, fj)
    if min_age is not None:
        obs = signal_values.get("account_age_days")
        obs_part = "missing" if obs is None else f"{float(obs):g}"
        matched["min_account_age_days"] = (
            f"threshold={min_age},observed={obs_part}"
        )

    return matched


def effective_min_account_age_days(
    rule_filters: RuleFilters,
    alert_fj: dict[str, Any],
) -> int | None:
    r = rule_filters.min_account_age_days
    raw_u = alert_fj.get("min_account_age_days")
    u: int | None
    if raw_u is None:
        u = None
    elif isinstance(raw_u, bool):
        u = None
    elif isinstance(raw_u, int):
        u = raw_u
    else:
        parsed = to_float(raw_u)
        u = int(parsed) if parsed is not None else None
    if r is not None and u is not None:
        return max(r, u)
    if u is not None:
        return u
    return r


def deferred_target_liquidity_usd(
    rule: AlertRuleV1,
    alert_fj: dict[str, Any],
) -> float | None:
    """Resolve liquidity target: alert override or server rule."""

    if alert_fj:
        u = to_float(alert_fj.get("target_liquidity_usd"))
        if u is not None:
            return u
    t = rule.deferred_watch.target_liquidity_usd
    return float(t) if t is not None else None


def deferred_ttl_hours(rule: AlertRuleV1, alert_fj: dict[str, Any]) -> int:
    raw = alert_fj.get("deferred_watch_ttl_hours") if alert_fj else None
    if raw is not None:
        v = to_float(raw)
        if v is not None and v >= 1:
            return int(v)
    return rule.deferred_watch.ttl_hours


# --- Pydantic models for API validation (per alert_type) ---


class BaseAlertFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_tags: list[str] = Field(default_factory=list)
    require_event_tag: str | None = Field(
        default=None,
        description="Optional: event must carry this tag (alert overrides server rule).",
    )

    @field_validator("category_tags", mode="before")
    @classmethod
    def _strip_tags(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [t.strip() for t in value if isinstance(t, str) and t.strip()]

    @field_validator("require_event_tag", mode="before")
    @classmethod
    def _norm_require_event_tag(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return normalize_tag(value)
        return value


class VolumeSpikeAlertFilters(BaseAlertFilters):
    """``filters_json`` for ``volume_spike_5m``."""

    return_1m_pct_min: float | None = None
    return_5m_pct_min: float | None = None
    spread_bps_max: float | None = None
    imbalance_abs_min: float | None = None
    liquidity_usd_min: float | None = None


class TraderPositionAlertFilters(BaseAlertFilters):
    """``filters_json`` for ``trader_position_update``."""

    return_1m_pct_min: float | None = None
    return_5m_pct_min: float | None = None
    spread_bps_max: float | None = None
    imbalance_abs_min: float | None = None
    liquidity_usd_min: float | None = None
    min_smart_score: float | None = Field(default=None, ge=0.0, le=100.0)
    min_account_age_days: int | None = Field(default=None, ge=0)


class NewMarketLiquidityAlertFilters(BaseAlertFilters):
    """``filters_json`` for ``new_market_liquidity``."""

    target_liquidity_usd: float | None = Field(default=None, ge=0.0)
    deferred_watch_ttl_hours: int | None = Field(default=None, ge=1)


def validated_filters_dict(
    alert_type: RuleType,
    raw: dict[str, str | int | float | bool | list[str]],
) -> dict[str, str | int | float | bool | list[str]]:
    """Validate ``raw`` against the allowlisted model for ``alert_type``."""

    if alert_type is RuleType.VOLUME_SPIKE_5M:
        return VolumeSpikeAlertFilters.model_validate(raw).model_dump(
            mode="json", exclude_none=True
        )
    if alert_type is RuleType.TRADER_POSITION_UPDATE:
        return TraderPositionAlertFilters.model_validate(raw).model_dump(
            mode="json", exclude_none=True
        )
    if alert_type is RuleType.NEW_MARKET_LIQUIDITY:
        return NewMarketLiquidityAlertFilters.model_validate(raw).model_dump(
            mode="json", exclude_none=True
        )
    raise ValueError(f"unsupported alert_type: {alert_type}")


def merge_filter_overrides(
    base: dict[str, str | int | float | bool | list[str]],
    overrides: dict[str, str | int | float | bool | list[str]],
) -> dict[str, str | int | float | bool | list[str]]:
    """Merge override key=value pairs into ``base`` (shallow copy)."""

    merged = dict(base)
    merged.update(overrides)
    return merged


# Slash-command options reserved for alert routing (not filter keys).
RESERVED_CREATE_OPTIONS: frozenset[str] = frozenset(
    {"alert_id", "cooldown", "enabled"}
)


_BOOL_FILTER_KEYS: frozenset[str] = frozenset()


def parse_filter_kv_line(line: str) -> dict[str, str | bool | list[str]]:
    """Parse ``k=v`` tokens from one line (wizard or CLI)."""

    raw: dict[str, str | bool | list[str]] = {}
    for token in line.split():
        if "=" not in token or token.startswith("="):
            continue
        key, value = token.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if key == "category_tags":
            raw[key] = [t.strip() for t in value.split(",") if t.strip()]
        elif key in _BOOL_FILTER_KEYS:
            raw[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        else:
            raw[key] = value.strip()
    return raw


def filters_from_command_options(
    options: dict[str, str],
    *,
    alert_type: RuleType,
    reserved: frozenset[str] | None = None,
) -> dict[str, str | int | float | bool | list[str]]:
    """Build validated ``filters_json`` from ``CommandArgs.options`` (key=value)."""

    skip = reserved if reserved is not None else RESERVED_CREATE_OPTIONS
    raw: dict[str, str | bool | list[str]] = {}
    for k, v in options.items():
        if k in skip:
            continue
        if k == "category_tags":
            raw[k] = [t.strip() for t in v.split(",") if t.strip()]
        elif k in _BOOL_FILTER_KEYS:
            raw[k] = v.strip().lower() in {"1", "true", "yes", "on"}
        else:
            raw[k] = v.strip()
    return validated_filters_dict(alert_type, raw)
