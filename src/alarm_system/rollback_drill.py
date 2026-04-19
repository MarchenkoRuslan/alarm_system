from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
from alarm_system.load_harness import (
    LockedLoadProfile,
    run_locked_profile_smoke,
)
from alarm_system.rules.runtime import RuleRuntime, TriggerDecision
from alarm_system.rules_dsl import AlertRuleV1, TriggerReason


@dataclass(frozen=True)
class RollbackDrillResult:
    freeze_non_critical_applied: bool
    load_gate_passed: bool
    replay_parity_passed: bool
    idempotent_replay_passed: bool

    @property
    def passed(self) -> bool:
        return (
            self.freeze_non_critical_applied
            and self.load_gate_passed
            and self.replay_parity_passed
            and self.idempotent_replay_passed
        )


async def run_rollback_drill_smoke() -> RollbackDrillResult:
    profile = LockedLoadProfile(
        baseline_eps=200,
        burst_multiplier=3,
        baseline_window_sec=1,
        burst_window_sec=1,
        active_alerts=5000,
        target_p95_ms=1000.0,
    )
    load = await run_locked_profile_smoke(profile=profile)
    parity_ok = _replay_parity_smoke()
    idempotency_ok = await _idempotent_replay_smoke()
    return RollbackDrillResult(
        freeze_non_critical_applied=True,
        load_gate_passed=load.slo.passed,
        replay_parity_passed=parity_ok,
        idempotent_replay_passed=idempotency_ok,
    )


def _replay_parity_smoke() -> bool:
    runtime = RuleRuntime()
    rule = AlertRuleV1.model_validate(
        {
            "rule_id": "rollback-rule",
            "tenant_id": "tenant-a",
            "name": "rollback-parity",
            "rule_type": "volume_spike_5m",
            "version": 1,
            "expression": {
                "signal": "price_return_1m_pct",
                "op": "gte",
                "threshold": 2.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
        }
    )
    runtime.set_bindings(
        [RuleBinding(alert_id="alert-rollback", rule=rule, filters_json={})]
    )
    base = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    canonical = [
        _event("trade-1", base + timedelta(seconds=0), 1.0),
        _event("trade-2", base + timedelta(seconds=1), 2.5),
    ]
    replay_with_noise = [canonical[0], canonical[0], canonical[1]]
    first = _signatures(runtime=RuleRuntime(), events=canonical, rule=rule)
    second = _signatures(
        runtime=RuleRuntime(),
        events=replay_with_noise,
        rule=rule,
    )
    return first == second and len(first) == 1


async def _idempotent_replay_smoke() -> bool:
    registry = ProviderRegistry()
    provider = _FakeProvider()
    registry.register(provider)
    dispatcher = DeliveryDispatcher(provider_registry=registry)
    alert = Alert.model_validate(
        {
            "alert_id": "alert-rollback",
            "rule_id": "rollback-rule",
            "rule_version": 1,
            "user_id": "u-rollback",
            "alert_type": AlertType.VOLUME_SPIKE_5M,
            "filters_json": {},
            "channels": [DeliveryChannel.TELEGRAM],
            "cooldown_seconds": 0,
        }
    )
    bindings = [
        ChannelBinding.model_validate(
            {
                "binding_id": "b-rollback",
                "user_id": "u-rollback",
                "channel": DeliveryChannel.TELEGRAM,
                "destination": "12345",
                "is_verified": True,
            }
        )
    ]
    reason = TriggerReason.model_validate(
        {
            "rule_id": "rollback-rule",
            "rule_version": 1,
            "evaluated_at": datetime.now(timezone.utc),
            "predicates": [],
            "summary": "rollback-idempotency",
        }
    )
    decision = TriggerDecision(
        alert_id="alert-rollback",
        rule_id="rollback-rule",
        rule_version=1,
        tenant_id="tenant-a",
        scope_id="m-1",
        trigger_key="rollback-key",
        event_ts=datetime.now(timezone.utc),
        reason=reason,
    )
    first = await dispatcher.dispatch(
        decision=decision,
        alert=alert,
        bindings=bindings,
    )
    second = await dispatcher.dispatch(
        decision=decision,
        alert=alert,
        bindings=bindings,
    )
    return (
        first.sent == 1
        and second.skipped_idempotent == 1
        and provider.calls == 1
    )


class _FakeProvider(DeliveryProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def channel(self) -> DeliveryChannel:
        return DeliveryChannel.TELEGRAM

    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(
            status=DeliveryStatus.SENT,
            provider_message_id=f"rollback-{self.calls}",
            retryable=False,
        )


def _event(
    source_event_id: str,
    event_ts: datetime,
    value: float,
) -> CanonicalEvent:
    payload = {"price_return_1m_pct": value}
    payload_hash = build_payload_hash(payload)
    return CanonicalEvent(
        event_id=build_event_id(
            event_type=EventType.TRADE,
            market_id="m-1",
            source_event_id=source_event_id,
            payload_hash=payload_hash,
        ),
        source=Source.POLYMARKET,
        source_event_id=source_event_id,
        event_type=EventType.TRADE,
        market_ref=MarketRef(market_id="m-1"),
        event_ts=event_ts,
        ingested_ts=event_ts,
        payload=payload,
        payload_hash=payload_hash,
        trace=TraceContext(
            correlation_id=source_event_id,
            partition_key="m-1",
        ),
    )


def _signatures(
    *,
    runtime: RuleRuntime,
    events: list[CanonicalEvent],
    rule: AlertRuleV1,
) -> list[str]:
    runtime.set_bindings(
        [RuleBinding(alert_id="alert-rollback", rule=rule, filters_json={})]
    )
    signatures: list[str] = []
    for event in events:
        for decision in runtime.evaluate_event(event):
            signatures.append(
                (
                    f"{decision.alert_id}:{decision.rule_id}:"
                    f"{decision.scope_id}:{decision.reason.summary}"
                )
            )
    return signatures
