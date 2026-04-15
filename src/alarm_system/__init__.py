"""Core contracts for Polymarket alerting MVP."""

from alarm_system.adapters import (
    AdapterEnvelope,
    AdapterRegistry,
    MarketAdapter,
    MarketSource,
)
from alarm_system.canonical_event import CanonicalEvent, EventType, Source
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.compute import FeatureSnapshot, PrefilterIndex, RuleBinding, extract_feature_snapshot
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
from alarm_system.rules import (
    DeferredWatchState,
    EvaluationResult,
    InMemoryDeferredWatchStore,
    RuleEvaluator,
    RuleRuntime,
    TriggerDecision,
)
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason

__all__ = [
    "AdapterEnvelope",
    "AdapterRegistry",
    "Alert",
    "AlertRuleV1",
    "AlertType",
    "CanonicalEvent",
    "DeferredWatchState",
    "ChannelBinding",
    "DeliveryAttempt",
    "DeliveryChannel",
    "DeliveryPayload",
    "DeliveryProvider",
    "DeliveryResult",
    "DeliveryStatus",
    "EvaluationResult",
    "Event",
    "EventType",
    "FeatureSnapshot",
    "InMemoryDeferredWatchStore",
    "PrefilterIndex",
    "MarketAdapter",
    "Market",
    "MarketSource",
    "ProviderRegistry",
    "RuleBinding",
    "RuleEvaluator",
    "RuleType",
    "RuleRuntime",
    "Source",
    "Trade",
    "Trader",
    "TriggerDecision",
    "TriggerReason",
    "User",
    "extract_feature_snapshot",
]
