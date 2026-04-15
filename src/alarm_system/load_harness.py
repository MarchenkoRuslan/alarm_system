from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from alarm_system.delivery import ProviderRegistry
from alarm_system.delivery_runtime import DeliveryDispatcher
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryChannel,
)
from alarm_system.observability import RuntimeObservability, SLOCheckResult
from alarm_system.rules.runtime import TriggerDecision
from alarm_system.rules_dsl import TriggerReason


@dataclass(frozen=True)
class LockedLoadProfile:
    baseline_eps: int = 200
    burst_multiplier: int = 3
    baseline_window_sec: int = 1
    burst_window_sec: int = 1
    active_alerts: int = 5000
    target_p95_ms: float = 1000.0


@dataclass(frozen=True)
class LoadHarnessResult:
    total_events: int
    baseline_events: int
    burst_events: int
    active_alerts: int
    slo: SLOCheckResult


async def run_locked_profile_smoke(
    profile: LockedLoadProfile | None = None,
) -> LoadHarnessResult:
    cfg = profile or LockedLoadProfile()
    observability = RuntimeObservability()
    dispatcher = DeliveryDispatcher(
        provider_registry=ProviderRegistry(),
        observability=observability,
    )

    alerts = [_build_alert(i) for i in range(cfg.active_alerts)]
    bindings = [_default_binding()]
    baseline_events = cfg.baseline_eps * cfg.baseline_window_sec
    burst_events = (
        cfg.baseline_eps
        * cfg.burst_multiplier
        * cfg.burst_window_sec
    )
    total_events = baseline_events + burst_events
    for idx in range(total_events):
        decision = _build_decision(seq=idx)
        alert = alerts[idx % len(alerts)]
        await dispatcher.dispatch(
            decision=decision,
            alert=alert,
            bindings=bindings,
            execute_sends=False,
        )
    slo = observability.check_event_to_enqueue_slo(cfg.target_p95_ms)
    return LoadHarnessResult(
        total_events=total_events,
        baseline_events=baseline_events,
        burst_events=burst_events,
        active_alerts=cfg.active_alerts,
        slo=slo,
    )


def _build_decision(seq: int) -> TriggerDecision:
    now = datetime.now(timezone.utc)
    reason = TriggerReason.model_validate(
        {
            "rule_id": "load-rule",
            "rule_version": 1,
            "evaluated_at": now,
            "predicates": [],
            "summary": f"load-harness-{seq}",
        }
    )
    return TriggerDecision(
        alert_id=f"alert-{seq % 5000}",
        rule_id="load-rule",
        rule_version=1,
        tenant_id="tenant-load",
        scope_id=f"m-{seq % 50}",
        trigger_key=f"load-trigger-{seq}",
        event_ts=now,
        reason=reason,
    )


def _build_alert(index: int) -> Alert:
    return Alert.model_validate(
        {
            "alert_id": f"alert-{index}",
            "rule_id": "load-rule",
            "rule_version": 1,
            "user_id": "u-load",
            "alert_type": AlertType.VOLUME_SPIKE_5M,
            "filters_json": {},
            "channels": [DeliveryChannel.TELEGRAM],
            "cooldown_seconds": 0,
        }
    )


def _default_binding() -> ChannelBinding:
    return ChannelBinding.model_validate(
        {
            "binding_id": "b-load",
            "user_id": "u-load",
            "channel": DeliveryChannel.TELEGRAM,
            "destination": "12345",
            "is_verified": True,
        }
    )
