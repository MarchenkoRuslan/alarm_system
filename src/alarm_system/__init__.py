"""Core contracts for Polymarket alerting MVP."""

from alarm_system.adapters import AdapterEnvelope, AdapterRegistry, MarketAdapter, MarketSource
from alarm_system.canonical_event import CanonicalEvent, EventType, Source
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryStatus,
    Event,
    Market,
    Trade,
    Trader,
    User,
)
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason

__all__ = [
    "AdapterEnvelope",
    "AdapterRegistry",
    "Alert",
    "AlertRuleV1",
    "AlertType",
    "CanonicalEvent",
    "ChannelBinding",
    "DeliveryAttempt",
    "DeliveryChannel",
    "DeliveryPayload",
    "DeliveryProvider",
    "DeliveryResult",
    "DeliveryStatus",
    "Event",
    "EventType",
    "MarketAdapter",
    "Market",
    "MarketSource",
    "ProviderRegistry",
    "RuleType",
    "Source",
    "Trade",
    "Trader",
    "TriggerReason",
    "User",
]
