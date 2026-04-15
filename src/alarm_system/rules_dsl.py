from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoolOp(str, Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


class CompareOp(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"
    DELTA = "delta"
    PERCENTILE = "percentile"
    ZSCORE = "zscore"


class RuleType(str, Enum):
    TRADER_POSITION_UPDATE = "trader_position_update"
    VOLUME_SPIKE_5M = "volume_spike_5m"
    NEW_MARKET_LIQUIDITY = "new_market_liquidity"


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_seconds: int = Field(gt=0)
    slide_seconds: int = Field(gt=0)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    op: CompareOp
    threshold: float
    window: Window
    market_scope: Literal["single_market", "event_group", "watchlist"] = "single_market"


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: BoolOp
    children: list["Expression"]

    @model_validator(mode="after")
    def _validate_not_children(self) -> "Group":
        if self.op is BoolOp.NOT and len(self.children) != 1:
            raise ValueError("NOT group must contain exactly one child expression")
        return self


Expression = Condition | Group


class SuppressIf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    op: CompareOp
    threshold: float
    duration_seconds: int = Field(gt=0)


class RuleFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_tags: list[str] = Field(default_factory=list)
    min_smart_score: float | None = Field(default=None, ge=0.0, le=100.0)
    min_account_age_days: int | None = Field(default=None, ge=0)
    iran_tag_only: bool = False


class DeferredWatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    target_liquidity_usd: float | None = Field(default=None, ge=0.0)
    ttl_hours: int = Field(default=24 * 14, ge=1)


class AlertRuleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    tenant_id: str
    name: str
    rule_type: RuleType
    severity: Literal["info", "warning", "critical"] = "warning"
    expression: Expression
    cooldown_seconds: int = Field(default=60, ge=0)
    suppress_if: list[SuppressIf] = Field(default_factory=list)
    filters: RuleFilters = Field(default_factory=RuleFilters)
    deferred_watch: DeferredWatchConfig = Field(default_factory=DeferredWatchConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)


class PredicateExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    op: CompareOp
    observed_value: float
    threshold: float
    passed: bool
    window_seconds: int
    note: str | None = None


class TriggerReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule_version: int
    evaluated_at: datetime
    predicates: list[PredicateExplanation]
    matched_filters: dict[str, str] = Field(default_factory=dict)
    summary: str


def stable_rule_checksum(rule: AlertRuleV1) -> str:
    stable = rule.model_dump(mode="json", exclude={"created_at"})
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_trigger_key(
    tenant_id: str,
    rule_id: str,
    rule_version: int,
    scope_id: str,
    bucket_seconds: int,
    at: datetime | None = None,
) -> str:
    ts = at or datetime.now(timezone.utc)
    bucket = int(ts.timestamp()) // bucket_seconds
    raw = f"{tenant_id}:{rule_id}:{rule_version}:{scope_id}:{bucket}"
    return sha256(raw.encode("utf-8")).hexdigest()


def cooldown_until(triggered_at: datetime, cooldown_seconds: int) -> datetime:
    return triggered_at + timedelta(seconds=cooldown_seconds)
