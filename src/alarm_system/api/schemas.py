from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from alarm_system.entities import Alert, ChannelBinding, DeliveryChannel
from alarm_system.rules_dsl import RuleType


ALERT_CREATE_EXAMPLES = {
    "user_a_trader_position_updates": {
        "summary": "User A: position updates in politics",
        "value": {
            "alert_id": "alert-user-a-trader-position-politics",
            "rule_id": "rule-user-a-trader-position-politics",
            "rule_version": 1,
            "user_id": "user-a",
            "alert_type": "trader_position_update",
            "filters_json": {},
            "cooldown_seconds": 60,
            "channels": ["telegram"],
            "enabled": True,
        },
    },
    "user_b_iran_volume_spike": {
        "summary": "User B: Iran volume spike",
        "value": {
            "alert_id": "alert-user-b-volume-iran",
            "rule_id": "rule-user-b-volume-iran",
            "rule_version": 1,
            "user_id": "user-b",
            "alert_type": "volume_spike_5m",
            "filters_json": {},
            "cooldown_seconds": 180,
            "channels": ["telegram"],
            "enabled": True,
        },
    },
    "user_c_new_market_liquidity": {
        "summary": "User C: new market liquidity",
        "value": {
            "alert_id": "alert-user-c-new-market-liquidity",
            "rule_id": "rule-user-c-new-market-liquidity",
            "rule_version": 1,
            "user_id": "user-c",
            "alert_type": "new_market_liquidity",
            "filters_json": {},
            "cooldown_seconds": 300,
            "channels": ["telegram"],
            "enabled": True,
        },
    },
}


CHANNEL_BINDING_UPSERT_EXAMPLES = {
    "user_a_telegram": {
        "summary": "User A Telegram binding",
        "value": {
            "binding_id": "tg-user-a",
            "user_id": "user-a",
            "channel": "telegram",
            "destination": "123456789",
            "is_verified": True,
            "settings_json": {},
        },
    },
    "user_b_telegram": {
        "summary": "User B Telegram binding",
        "value": {
            "binding_id": "tg-user-b",
            "user_id": "user-b",
            "channel": "telegram",
            "destination": "987654321",
            "is_verified": True,
            "settings_json": {},
        },
    },
}


class AlertCreateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [item["value"] for item in ALERT_CREATE_EXAMPLES.values()]},
    )

    alert_id: str | None = None
    rule_id: str
    rule_version: int = Field(default=1, ge=1)
    user_id: str
    alert_type: RuleType
    filters_json: dict[str, str | int | float | bool | list[str]]
    cooldown_seconds: int = Field(default=60, ge=0)
    channels: list[DeliveryChannel] = Field(
        default_factory=lambda: [DeliveryChannel.TELEGRAM]
    )
    enabled: bool = True

    def to_alert(self) -> Alert:
        return Alert.model_validate(
            {
                "alert_id": self.alert_id or f"alert-{uuid4()}",
                "rule_id": self.rule_id,
                "rule_version": self.rule_version,
                "user_id": self.user_id,
                "alert_type": self.alert_type,
                "filters_json": self.filters_json,
                "cooldown_seconds": self.cooldown_seconds,
                "channels": self.channels,
                "enabled": self.enabled,
                "created_at": datetime.now(timezone.utc),
            }
        )


class AlertUpdateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "rule_id": "rule-user-b-volume-iran",
                    "rule_version": 1,
                    "user_id": "user-b",
                    "alert_type": "volume_spike_5m",
                    "filters_json": {},
                    "cooldown_seconds": 180,
                    "channels": ["telegram"],
                    "enabled": True,
                    "expected_version": 2,
                }
            ]
        },
    )

    rule_id: str
    rule_version: int = Field(default=1, ge=1)
    user_id: str
    alert_type: RuleType
    filters_json: dict[str, str | int | float | bool | list[str]]
    cooldown_seconds: int = Field(default=60, ge=0)
    channels: list[DeliveryChannel] = Field(
        default_factory=lambda: [DeliveryChannel.TELEGRAM]
    )
    enabled: bool = True
    expected_version: int = Field(ge=1)

    def to_alert(
        self,
        *,
        alert_id: str,
        created_at: datetime,
    ) -> Alert:
        return Alert.model_validate(
            {
                "alert_id": alert_id,
                "rule_id": self.rule_id,
                "rule_version": self.rule_version,
                "user_id": self.user_id,
                "alert_type": self.alert_type,
                "filters_json": self.filters_json,
                "cooldown_seconds": self.cooldown_seconds,
                "channels": self.channels,
                "enabled": self.enabled,
                "created_at": created_at,
            }
        )


class AlertResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert: Alert


class AlertListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[Alert]


class ChannelBindingUpsertRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                item["value"]
                for item in CHANNEL_BINDING_UPSERT_EXAMPLES.values()
            ]
        },
    )

    binding_id: str | None = None
    user_id: str
    channel: DeliveryChannel
    destination: str
    is_verified: bool = False
    settings_json: dict[str, str | int | bool] = Field(default_factory=dict)

    def to_binding(self) -> ChannelBinding:
        return ChannelBinding.model_validate(
            {
                "binding_id": self.binding_id or f"binding-{uuid4()}",
                "user_id": self.user_id,
                "channel": self.channel,
                "destination": self.destination,
                "is_verified": self.is_verified,
                "settings_json": self.settings_json,
                "created_at": datetime.now(timezone.utc),
            }
        )


class ChannelBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: ChannelBinding


class ChannelBindingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[ChannelBinding]
