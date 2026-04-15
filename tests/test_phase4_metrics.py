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
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.observability import RuntimeObservability
from alarm_system.rules.runtime import RuleRuntime, TriggerDecision
from alarm_system.rules_dsl import AlertRuleV1, TriggerReason


class _FakeTelegramProvider(DeliveryProvider):
    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        return DeliveryResult(
            status=DeliveryStatus.SENT,
            provider_message_id="msg-1",
            retryable=False,
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


def _decision() -> TriggerDecision:
    reason = TriggerReason.model_validate(
        {
            "rule_id": "r-1",
            "rule_version": 1,
            "evaluated_at": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            "predicates": [],
            "summary": "phase4-metrics",
        }
    )
    return TriggerDecision(
        alert_id="alert-1",
        rule_id="r-1",
        rule_version=1,
        tenant_id="tenant-a",
        scope_id="m-1",
        trigger_key="phase4-metrics-key",
        event_ts=datetime.now(timezone.utc),
        reason=reason,
    )


class Phase4MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_records_queue_lag_metric(self) -> None:
        provider = _FakeTelegramProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        observability = RuntimeObservability()
        dispatcher = DeliveryDispatcher(
            provider_registry=registry,
            observability=observability,
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

        await dispatcher.dispatch(
            decision=_decision(),
            alert=alert,
            bindings=bindings,
        )
        snapshot = observability.snapshot()

        self.assertIn("queue_lag_ms", snapshot["p95_timings_ms"])
        self.assertGreaterEqual(observability.p95_ms("queue_lag_ms"), 0.0)
        self.assertIn(
            "queue_lag_ms|channel=telegram",
            snapshot["series"]["p95_timings_ms"],
        )

    def test_runtime_records_rule_eval_and_dedup_hits(self) -> None:
        observability = RuntimeObservability()
        runtime = RuleRuntime(observability=observability)
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-metrics",
                "tenant_id": "tenant-a",
                "name": "Metrics rule",
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
        runtime.set_bindings(
            [RuleBinding(alert_id="alert-metrics", rule=rule)]
        )
        event = _event(
            event_type=EventType.TRADE,
            market_id="m-1",
            source_event_id="trade-1",
            event_ts=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            payload={"price_return_1m_pct": 1.5},
        )

        first = runtime.evaluate_event(event)
        second = runtime.evaluate_event(event)
        snapshot = observability.snapshot()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertGreater(observability.p95_ms("rule_eval_ms"), 0.0)
        self.assertEqual(observability.count("dedup_hits_total"), 1)
        self.assertIn("rule_eval_ms", snapshot["p95_timings_ms"])
        self.assertIn("dedup_hits_total", snapshot["counters"])

    def test_observability_labeled_series_are_aggregated(self) -> None:
        observability = RuntimeObservability()
        observability.observe_timing_ms(
            "rule_eval_ms",
            10.0,
            labels={"rule_type": "volume_spike_5m", "event_type": "trade"},
        )
        observability.observe_timing_ms(
            "rule_eval_ms",
            20.0,
            labels={"rule_type": "volume_spike_5m", "event_type": "trade"},
        )
        observability.increment(
            "dedup_hits_total",
            labels={"rule_type": "volume_spike_5m", "event_type": "trade"},
        )
        snapshot = observability.snapshot()

        self.assertIn(
            "rule_eval_ms|event_type=trade,rule_type=volume_spike_5m",
            snapshot["series"]["p95_timings_ms"],
        )
        self.assertIn(
            "dedup_hits_total|event_type=trade,rule_type=volume_spike_5m",
            snapshot["series"]["counters"],
        )
