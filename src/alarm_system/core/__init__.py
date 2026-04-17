"""Curated shared core surface for API and Worker apps."""

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreContractError,
    AlertStoreConflictError,
    CachedAlertStore,
    InMemoryAlertStore,
    PostgresAlertStore,
    RedisAlertCache,
)
from alarm_system.canonical_event import CanonicalEvent, EventType, Source
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.delivery_runtime import DeliveryDispatcher, DispatchStats
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.observability import RuntimeObservability, SLOCheckResult
from alarm_system.rules import RuleRuntime, TriggerDecision
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason
from alarm_system.state import TriggerAuditRecord

__all__ = [
    "Alert",
    "AlertRuleV1",
    "AlertStoreBackendError",
    "AlertStoreConflictError",
    "AlertStoreContractError",
    "AlertType",
    "CachedAlertStore",
    "CanonicalEvent",
    "ChannelBinding",
    "DeliveryAttempt",
    "DeliveryChannel",
    "DeliveryDispatcher",
    "DeliveryPayload",
    "DeliveryProvider",
    "DeliveryResult",
    "DeliveryStatus",
    "DispatchStats",
    "EventType",
    "InMemoryAlertStore",
    "PostgresAlertStore",
    "ProviderRegistry",
    "RedisAlertCache",
    "RuleRuntime",
    "RuleType",
    "RuntimeObservability",
    "SLOCheckResult",
    "Source",
    "TriggerAuditRecord",
    "TriggerDecision",
    "TriggerReason",
]
