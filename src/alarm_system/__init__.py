"""Core contracts for Polymarket alerting MVP."""

from alarm_system.adapters import (
    AdapterEnvelope,
    AdapterRegistry,
    MarketAdapter,
    MarketSource,
)
from alarm_system.backpressure import BackpressureController, BackpressureSnapshot
from alarm_system.canonical_event import CanonicalEvent, EventType, Source
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.delivery_runtime import (
    DeliveryDispatcher,
    DispatchStats,
    EnqueuedDelivery,
)
from alarm_system.compute import (
    FeatureSnapshot,
    PrefilterIndex,
    RuleBinding,
    extract_feature_snapshot,
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
from alarm_system.rules import (
    DeferredWatchState,
    EvaluationResult,
    InMemoryDeferredWatchStore,
    RedisBackedDeferredWatchStore,
    RedisSuppressionStore,
    RuleEvaluator,
    RuleRuntime,
    TriggerDecision,
)
from alarm_system.observability import RuntimeObservability, SLOCheckResult
from alarm_system.load_harness import (
    LoadHarnessResult,
    LockedLoadProfile,
    run_locked_profile_smoke,
)
from alarm_system.rollback_drill import (
    RollbackDrillResult,
    run_rollback_drill_smoke,
)
from alarm_system.providers import TelegramProvider
from alarm_system.state import (
    InMemoryDeliveryIdempotencyStore,
    InMemoryCooldownStore,
    InMemoryDeliveryAttemptStore,
    InMemoryTriggerAuditStore,
    InMemoryTriggerDedupStore,
    RedisCooldownStore,
    RedisDeliveryAttemptStore,
    RedisDeliveryIdempotencyStore,
    RedisDeferredWatchStore,
    RedisSuppressionStateStore,
    RedisTriggerAuditStore,
    RedisTriggerDedupStore,
    TriggerAuditRecord,
)
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason

__all__ = [
    "AdapterEnvelope",
    "AdapterRegistry",
    "Alert",
    "AlertRuleV1",
    "AlertType",
    "CanonicalEvent",
    "BackpressureController",
    "BackpressureSnapshot",
    "DeferredWatchState",
    "ChannelBinding",
    "DeliveryAttempt",
    "DeliveryChannel",
    "DeliveryPayload",
    "DeliveryProvider",
    "DeliveryResult",
    "DeliveryDispatcher",
    "DispatchStats",
    "EnqueuedDelivery",
    "DeliveryStatus",
    "EvaluationResult",
    "Event",
    "EventType",
    "FeatureSnapshot",
    "InMemoryDeferredWatchStore",
    "InMemoryCooldownStore",
    "InMemoryDeliveryIdempotencyStore",
    "InMemoryDeliveryAttemptStore",
    "InMemoryTriggerAuditStore",
    "InMemoryTriggerDedupStore",
    "PrefilterIndex",
    "MarketAdapter",
    "Market",
    "MarketSource",
    "ProviderRegistry",
    "RedisBackedDeferredWatchStore",
    "RedisCooldownStore",
    "RedisDeliveryAttemptStore",
    "RedisDeliveryIdempotencyStore",
    "RedisDeferredWatchStore",
    "RedisSuppressionStateStore",
    "RedisSuppressionStore",
    "RedisTriggerAuditStore",
    "RedisTriggerDedupStore",
    "RuleBinding",
    "RuleEvaluator",
    "RuleType",
    "RuleRuntime",
    "RuntimeObservability",
    "SLOCheckResult",
    "LoadHarnessResult",
    "LockedLoadProfile",
    "RollbackDrillResult",
    "Source",
    "TelegramProvider",
    "Trade",
    "Trader",
    "TriggerDecision",
    "TriggerAuditRecord",
    "TriggerReason",
    "User",
    "extract_feature_snapshot",
    "run_locked_profile_smoke",
    "run_rollback_drill_smoke",
]
