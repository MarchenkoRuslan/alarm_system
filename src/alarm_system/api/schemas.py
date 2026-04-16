from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from alarm_system.entities import Alert, ChannelBinding, DeliveryChannel
from alarm_system.rules_dsl import RuleType


class AlertCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
