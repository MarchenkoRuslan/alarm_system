from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from alarm_system.rules_dsl import RuleType


AlertType = RuleType


class DeliveryChannel(str, Enum):
    TELEGRAM = "telegram"
    EMAIL = "email"
    WEBHOOK = "webhook"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


class User(BaseModel):
    """Platform user. Channel-specific contact info lives in ChannelBinding."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str | None = None
    timezone_name: str = "UTC"
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChannelBinding(BaseModel):
    """
    One verified delivery contact for a user on a specific channel.

    - TELEGRAM: destination = telegram chat_id
    - EMAIL:    destination = email address
    - WEBHOOK:  destination = URL; settings_json may include headers, method
    """

    model_config = ConfigDict(extra="forbid")

    binding_id: str
    user_id: str
    channel: DeliveryChannel
    destination: str
    is_verified: bool = False
    settings_json: dict[str, str | int | bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Alert(BaseModel):
    """
    User alert routing config bound to an immutable rule version.
    `alert_id` is delivery/audit identity, `rule_id` is evaluation identity.
    channels defaults to [TELEGRAM] for backward compatibility.
    """

    model_config = ConfigDict(extra="forbid")

    alert_id: str
    rule_id: str
    rule_version: int = Field(default=1, ge=1)
    user_id: str
    alert_type: AlertType
    filters_json: dict[str, str | int | float | bool | list[str]]
    cooldown_seconds: int = Field(default=60, ge=0)
    channels: list[DeliveryChannel] = Field(default_factory=lambda: [DeliveryChannel.TELEGRAM])
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeliveryAttempt(BaseModel):
    """Audit record for a single delivery attempt to one channel."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    trigger_id: str
    alert_id: str
    channel: DeliveryChannel
    destination: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempt_no: int = Field(default=1, ge=1)
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: datetime | None = None
    next_retry_at: datetime | None = None


class Market(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    event_id: str
    title: str
    category_tags: list[str]
    created_at: datetime
    current_liquidity_usd: float = Field(default=0.0, ge=0.0)
    last_liquidity_update_ts: datetime | None = None


class Trader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trader_id: str
    wallet_address: str
    smart_score: float = Field(ge=0.0, le=100.0)
    account_age_days: int = Field(ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Trade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_id: str
    market_id: str
    trader_id: str
    side: str
    size: float = Field(gt=0)
    price: float = Field(ge=0.0, le=1.0)
    notional_usd: float = Field(ge=0.0)
    traded_at: datetime


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    market_id: str
    event_type: str
    payload: dict[str, str | int | float | bool | list[str] | dict[str, str]]
    event_ts: datetime
