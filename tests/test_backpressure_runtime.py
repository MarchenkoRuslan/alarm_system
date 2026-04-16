from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from alarm_system.backpressure import BackpressureController
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
from alarm_system.observability import RuntimeObservability
from alarm_system.rules.runtime import TriggerDecision
from alarm_system.rules_dsl import TriggerReason


class _BlockingTelegramProvider(DeliveryProvider):
    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self.calls = 0

    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        self.calls += 1
        await self._gate.wait()
        return DeliveryResult(
            status=DeliveryStatus.SENT,
            provider_message_id=f"msg-{self.calls}",
            retryable=False,
        )


def _decision(seq: int) -> TriggerDecision:
    reason = TriggerReason.model_validate(
        {
            "rule_id": "r-1",
            "rule_version": 1,
            "evaluated_at": datetime.now(timezone.utc),
            "predicates": [],
            "summary": f"trigger-{seq}",
        }
    )
    return TriggerDecision(
        alert_id="alert-1",
        rule_id="r-1",
        rule_version=1,
        tenant_id="tenant-a",
        scope_id="m-1",
        trigger_key=f"backpressure-trigger-{seq}",
        event_ts=datetime.now(timezone.utc),
        reason=reason,
    )


def _alert() -> Alert:
    return Alert.model_validate(
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


def _bindings() -> list[ChannelBinding]:
    return [
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


class BackpressureControllerTests(unittest.TestCase):
    def test_state_moves_warning_critical_and_back_to_normal(self) -> None:
        controller = BackpressureController(capacity=10, recovery_window_samples=2)
        for _ in range(7):
            self.assertTrue(controller.reserve_slot())
        self.assertEqual(controller.snapshot().state, "warning")
        for _ in range(2):
            self.assertTrue(controller.reserve_slot())
        self.assertEqual(controller.snapshot().state, "critical")
        controller.release_slot()
        self.assertEqual(controller.snapshot().state, "warning")
        for _ in range(10):
            controller.release_slot()
        self.assertEqual(controller.snapshot().state, "normal")


class DeliveryBackpressureAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_warning_state_acceptance_keeps_dispatch_correct(self) -> None:
        gate = asyncio.Event()
        provider = _BlockingTelegramProvider(gate)
        registry = ProviderRegistry()
        registry.register(provider)
        observability = RuntimeObservability()
        controller = BackpressureController(capacity=10)
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            observability=observability,
            backpressure=controller,
        )

        async def _dispatch(i: int):
            return await dispatcher.dispatch(
                decision=_decision(i),
                alert=_alert(),
                bindings=_bindings(),
            )

        tasks = [asyncio.create_task(_dispatch(i)) for i in range(8)]
        await asyncio.sleep(0.05)
        self.assertEqual(controller.snapshot().state, "warning")
        gate.set()
        results = await asyncio.gather(*tasks)

        self.assertEqual(sum(item.sent for item in results), 8)
        self.assertEqual(sum(item.skipped_backpressure for item in results), 0)
        self.assertEqual(provider.calls, 8)
        self.assertLessEqual(observability.p95_ms("event_to_enqueue_ms"), 1000.0)

    async def test_critical_state_rejects_when_capacity_exceeded(self) -> None:
        gate = asyncio.Event()
        provider = _BlockingTelegramProvider(gate)
        registry = ProviderRegistry()
        registry.register(provider)
        observability = RuntimeObservability()
        controller = BackpressureController(capacity=10)
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            observability=observability,
            backpressure=controller,
        )

        async def _dispatch(i: int):
            return await dispatcher.dispatch(
                decision=_decision(i),
                alert=_alert(),
                bindings=_bindings(),
            )

        tasks = [asyncio.create_task(_dispatch(i)) for i in range(12)]
        await asyncio.sleep(0.05)
        self.assertEqual(controller.snapshot().state, "critical")
        gate.set()
        results = await asyncio.gather(*tasks)

        self.assertEqual(sum(item.sent for item in results), 10)
        self.assertEqual(sum(item.skipped_backpressure for item in results), 2)
        self.assertEqual(provider.calls, 10)
        self.assertGreater(observability.count("backpressure_rejected_total"), 0)

    async def test_recovery_state_returns_to_normal_without_duplicates(self) -> None:
        gate = asyncio.Event()
        provider = _BlockingTelegramProvider(gate)
        registry = ProviderRegistry()
        registry.register(provider)
        controller = BackpressureController(capacity=10, recovery_window_samples=2)
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            backpressure=controller,
        )

        async def _dispatch(i: int):
            return await dispatcher.dispatch(
                decision=_decision(i),
                alert=_alert(),
                bindings=_bindings(),
            )

        tasks = [asyncio.create_task(_dispatch(i)) for i in range(10)]
        await asyncio.sleep(0.05)
        self.assertEqual(controller.snapshot().state, "critical")
        gate.set()
        first_batch = await asyncio.gather(*tasks)

        self.assertEqual(sum(item.sent for item in first_batch), 10)
        self.assertEqual(controller.snapshot().state, "normal")

        next_result = await dispatcher.dispatch(
            decision=_decision(999),
            alert=_alert(),
            bindings=_bindings(),
        )
        duplicate_result = await dispatcher.dispatch(
            decision=_decision(999),
            alert=_alert(),
            bindings=_bindings(),
        )

        self.assertEqual(next_result.sent, 1)
        self.assertEqual(duplicate_result.skipped_idempotent, 1)
        self.assertEqual(controller.snapshot().state, "normal")
