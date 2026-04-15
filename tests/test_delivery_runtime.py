from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alarm_system.canonical_event import (
    CanonicalEvent,
    EventType,
    MarketRef,
    Source,
    TraceContext,
    build_event_id,
    build_payload_hash,
)
from alarm_system.compute.prefilter import RuleBinding
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.delivery_runtime import DeliveryDispatcher
from alarm_system.observability import RuntimeObservability
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.rules.runtime import RuleRuntime, TriggerDecision
from alarm_system.rules_dsl import AlertRuleV1, TriggerReason
from alarm_system.state import (
    CooldownStore,
    InMemoryDeliveryAttemptStore,
    InMemoryDeliveryIdempotencyStore,
    InMemoryTriggerAuditStore,
)


class _FakeTelegramProvider(DeliveryProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(
            status=DeliveryStatus.SENT,
            provider_message_id=f"msg-{self.calls}",
            retryable=False,
        )


class _BlockThenAllowCooldownStore(CooldownStore):
    def __init__(self) -> None:
        self.calls = 0

    def allow(
        self,
        *,
        tenant_id: str,
        rule_id: str,
        rule_version: int,
        scope_id: str,
        channel: DeliveryChannel,
        triggered_at: datetime,
        cooldown_seconds: int,
    ) -> bool:
        self.calls += 1
        return self.calls > 1


class _RetryThenFailProvider(DeliveryProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        self.calls += 1
        if self.calls < 3:
            return DeliveryResult(
                status=DeliveryStatus.FAILED,
                error_code="temporary",
                error_detail="temporary",
                retryable=True,
            )
        return DeliveryResult(
            status=DeliveryStatus.FAILED,
            error_code="permanent",
            error_detail="permanent",
            retryable=False,
        )


def _decision() -> TriggerDecision:
    reason = TriggerReason.model_validate(
        {
            "rule_id": "r-1",
            "rule_version": 1,
            "evaluated_at": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            "predicates": [],
            "summary": "VolumeSpike5m(2.35>2.00) on Iran-tag market",
        }
    )
    return TriggerDecision(
        alert_id="alert-1",
        rule_id="r-1",
        rule_version=1,
        tenant_id="tenant-a",
        scope_id="m-1",
        trigger_key="abcdef1234567890",
        event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        reason=reason,
    )


def _event(
    *,
    event_type: EventType,
    market_id: str,
    source_event_id: str,
    event_ts: datetime,
    payload: dict[str, object],
) -> CanonicalEvent:
    payload_hash = build_payload_hash(payload)
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=event_type,
            market_id=market_id,
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=event_type,
        market_ref=MarketRef(market_id=market_id),
        event_ts=event_ts,
        ingested_ts=event_ts,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(
            correlation_id=source_event_id,
            partition_key=market_id,
        ),
    )


class DeliveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_persists_reason_json_and_delivery_attempt(
        self,
    ) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        attempts = InMemoryDeliveryAttemptStore()
        audits = InMemoryTriggerAuditStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            attempt_store=attempts,
            trigger_audit_store=audits,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        stats = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )

        self.assertEqual(stats.queued, 1)
        self.assertEqual(stats.sent, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(attempts.all()), 1)
        self.assertEqual(len(audits.all()), 1)
        reason_json = audits.all()[0].to_reason_json()
        self.assertIn("VolumeSpike5m", reason_json)

    async def test_dispatch_is_idempotent_for_same_trigger_channel_destination(
        self,
    ) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        dispatcher = DeliveryDispatcher(provider_registry=registry)
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        first = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )
        second = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )

        self.assertEqual(first.sent, 1)
        self.assertEqual(second.skipped_idempotent, 1)
        self.assertEqual(provider.calls, 1)

    async def test_dispatch_is_idempotent_across_dispatcher_instances(
        self,
    ) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        shared_idempotency = InMemoryDeliveryIdempotencyStore()
        shared_audits = InMemoryTriggerAuditStore()
        first_dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            delivery_idempotency_store=shared_idempotency,
            trigger_audit_store=shared_audits,
        )
        second_dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            delivery_idempotency_store=shared_idempotency,
            trigger_audit_store=shared_audits,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        first = await first_dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )
        second = await second_dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )

        self.assertEqual(first.sent, 1)
        self.assertEqual(second.skipped_idempotent, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(shared_audits.all()), 1)

    async def test_cooldown_rejection_does_not_consume_idempotency(
        self,
    ) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        shared_idempotency = InMemoryDeliveryIdempotencyStore()
        cooldown = _BlockThenAllowCooldownStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            cooldown_store=cooldown,
            delivery_idempotency_store=shared_idempotency,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 60,
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        first = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )
        second = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )

        self.assertEqual(first.skipped_cooldown, 1)
        self.assertEqual(first.skipped_idempotent, 0)
        self.assertEqual(second.sent, 1)
        self.assertEqual(second.skipped_idempotent, 0)
        self.assertEqual(provider.calls, 1)

    async def test_retry_and_failure_attempts_are_persisted(self) -> None:
        provider = _RetryThenFailProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        attempts = InMemoryDeliveryAttemptStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            attempt_store=attempts,
            max_attempts=3,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        stats = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )

        self.assertEqual(stats.failed, 1)
        self.assertEqual(provider.calls, 3)
        saved = attempts.all()
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved[0].status, DeliveryStatus.RETRYING)
        self.assertEqual(saved[1].status, DeliveryStatus.RETRYING)
        self.assertEqual(saved[2].status, DeliveryStatus.FAILED)
        self.assertIsNotNone(saved[0].next_retry_at)
        self.assertIsNotNone(saved[1].next_retry_at)
        self.assertIsNone(saved[2].next_retry_at)

    async def test_unverified_binding_does_not_consume_idempotency(
        self,
    ) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        shared_idempotency = InMemoryDeliveryIdempotencyStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            delivery_idempotency_store=shared_idempotency,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-1",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        )
        unverified_bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": False,
                }
            )
        ]
        verified_bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        first = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=unverified_bindings,
        )
        second = await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=verified_bindings,
        )

        self.assertEqual(first.skipped_missing_binding, 1)
        self.assertEqual(first.skipped_idempotent, 0)
        self.assertEqual(second.sent, 1)
        self.assertEqual(second.skipped_idempotent, 0)
        self.assertEqual(provider.calls, 1)

    async def test_runtime_decision_enqueue_boundary_records_slo_metric(
        self,
    ) -> None:
        runtime = RuleRuntime()
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-enqueue",
                "tenant_id": "tenant-a",
                "name": "enqueue-boundary",
                "rule_type": "volume_spike_5m",
                "version": 1,
                "expression": {
                    "signal": "price_return_1m_pct",
                    "op": "gte",
                    "threshold": 1.0,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
            }
        )
        runtime.set_bindings([RuleBinding(alert_id="alert-1", rule=rule)])
        event_at = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
        event = _event(
            event_type=EventType.TRADE,
            market_id="m-1",
            source_event_id="trade-1",
            event_ts=event_at,
            payload={"price_return_1m_pct": 1.5, "tags": ["politics"]},
        )
        decisions = runtime.evaluate_event(event)
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]

        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        observability = RuntimeObservability()
        audits = InMemoryTriggerAuditStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            trigger_audit_store=audits,
            observability=observability,
        )
        alert = Alert.model_validate(
            {
                "alert_id": "alert-1",
                "rule_id": "r-enqueue",
                "rule_version": 1,
                "user_id": "u-1",
                "alert_type": AlertType.VOLUME_SPIKE_5M,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        )
        bindings = [
            ChannelBinding.model_validate(
                {
                    "binding_id": "b-1",
                    "user_id": "u-1",
                    "channel": DeliveryChannel.TELEGRAM,
                    "destination": "12345",
                    "is_verified": True,
                }
            )
        ]

        stats = await dispatcher.dispatch(
            decision=decision,
            alert=alert,
            bindings=bindings,
            execute_sends=False,
        )

        self.assertEqual(stats.queued, 1)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(len(audits.all()), 1)
        slo = observability.check_event_to_enqueue_slo()
        self.assertTrue(slo.passed)
