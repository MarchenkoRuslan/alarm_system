from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryProvider,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.delivery_runtime import DeliveryDispatcher
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.rules.runtime import TriggerDecision
from alarm_system.rules_dsl import TriggerReason
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

    async def test_dispatch_is_idempotent_across_dispatcher_instances(self) -> None:
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

    async def test_cooldown_rejection_does_not_consume_idempotency(self) -> None:
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
