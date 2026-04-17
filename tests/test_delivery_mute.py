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
from alarm_system.observability import RuntimeObservability
from alarm_system.state import (
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
)


class _CountingProvider(DeliveryProvider):
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


def _decision() -> TriggerDecision:
    reason = TriggerReason.model_validate(
        {
            "rule_id": "r-1",
            "rule_version": 1,
            "evaluated_at": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            "predicates": [],
            "summary": "Muted test",
        }
    )
    return TriggerDecision(
        alert_id="alert-1",
        rule_id="r-1",
        rule_version=1,
        tenant_id="tenant-a",
        scope_id="m-1",
        trigger_key="muted-trigger-key-1",
        event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        reason=reason,
    )


def _alert() -> Alert:
    return Alert.model_validate(
        {
            "alert_id": "alert-1",
            "rule_id": "r-1",
            "rule_version": 1,
            "user_id": "42",
            "alert_type": AlertType.VOLUME_SPIKE_5M,
            "filters_json": {},
            "channels": [DeliveryChannel.TELEGRAM],
            "cooldown_seconds": 0,
        }
    )


def _binding() -> ChannelBinding:
    return ChannelBinding.model_validate(
        {
            "binding_id": "b-1",
            "user_id": "42",
            "channel": DeliveryChannel.TELEGRAM,
            "destination": "500",
            "is_verified": True,
        }
    )


class DeliveryMuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_mute_skips_send_and_increments_skipped_muted(self) -> None:
        provider = _CountingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        mute_store = InMemoryMuteStore()
        mute_store.set_mute(user_id="42", seconds=60)
        attempts = InMemoryDeliveryAttemptStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            mute_store=mute_store,
            attempt_store=attempts,
        )

        stats = await dispatcher.dispatch(
            decision=_decision(),
            alert=_alert(),
            bindings=[_binding()],
        )

        self.assertEqual(stats.skipped_muted, 1)
        self.assertEqual(stats.sent, 0)
        self.assertEqual(stats.queued, 0)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(attempts.all(), [])

    async def test_unmuted_user_receives_delivery(self) -> None:
        provider = _CountingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            mute_store=InMemoryMuteStore(),
        )

        stats = await dispatcher.dispatch(
            decision=_decision(),
            alert=_alert(),
            bindings=[_binding()],
        )

        self.assertEqual(stats.sent, 1)
        self.assertEqual(stats.skipped_muted, 0)
        self.assertEqual(provider.calls, 1)

    async def test_mute_store_error_fails_open_and_is_observed(self) -> None:
        class _BrokenMuteStore:
            def get_mute_until(self, user_id: str):
                raise RuntimeError("redis down")

            def set_mute(self, *, user_id: str, seconds: int):  # pragma: no cover
                raise NotImplementedError

            def clear_mute(self, user_id: str):  # pragma: no cover
                raise NotImplementedError

        provider = _CountingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        observability = RuntimeObservability()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            mute_store=_BrokenMuteStore(),
            observability=observability,
        )

        stats = await dispatcher.dispatch(
            decision=_decision(),
            alert=_alert(),
            bindings=[_binding()],
        )

        # Fail-open: delivery proceeds even though mute check failed.
        self.assertEqual(stats.sent, 1)
        self.assertEqual(
            observability.count("delivery_mute_check_failed_total"),
            1,
        )

    async def test_dispatcher_populates_per_user_history(self) -> None:
        provider = _CountingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        attempts = InMemoryDeliveryAttemptStore()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            attempt_store=attempts,
        )

        await dispatcher.dispatch(
            decision=_decision(),
            alert=_alert(),
            bindings=[_binding()],
        )

        history = attempts.list_by_user(user_id="42", limit=5)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].alert_id, "alert-1")
        self.assertEqual(history[0].status, DeliveryStatus.SENT)
        # Non-owner gets nothing.
        self.assertEqual(attempts.list_by_user(user_id="99", limit=5), [])
