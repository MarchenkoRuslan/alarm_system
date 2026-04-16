from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

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
from alarm_system.delivery import ProviderRegistry
from alarm_system.delivery_runtime import DeliveryDispatcher
from alarm_system.entities import (
    Alert,
    AlertType,
    ChannelBinding,
    DeliveryChannel,
)
from alarm_system.observability import RuntimeObservability, SLOCheckResult
from alarm_system.rules.runtime import RuleRuntime, TriggerDecision
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason


@dataclass(frozen=True)
class LockedLoadProfile:
    baseline_eps: int = 200
    burst_multiplier: int = 3
    baseline_window_sec: int = 1
    burst_window_sec: int = 1
    active_alerts: int = 5000
    target_p95_ms: float = 1000.0
    run_end_to_end: bool = True
    tag_buckets: int = 100
    progress_every_events: int = 0
    max_runtime_sec: float | None = None
    min_queued_ratio: float = 0.25


@dataclass(frozen=True)
class LongBurstLoadProfile(LockedLoadProfile):
    baseline_window_sec: int = 60
    burst_window_sec: int = 60
    tag_buckets: int = 5000


@dataclass(frozen=True)
class LoadHarnessResult:
    total_events: int
    baseline_events: int
    burst_events: int
    active_alerts: int
    decisions_emitted: int
    dispatched_queued: int
    slo: SLOCheckResult


class LoadHarnessTimeoutError(RuntimeError):
    pass


async def run_locked_profile_smoke(
    profile: LockedLoadProfile | None = None,
) -> LoadHarnessResult:
    cfg = profile or LockedLoadProfile()
    if cfg.run_end_to_end:
        return await run_locked_profile_end_to_end(profile=cfg)
    return await _run_locked_profile_dispatch_only(profile=cfg)


async def run_locked_profile_end_to_end(
    profile: LockedLoadProfile | None = None,
) -> LoadHarnessResult:
    cfg = profile or LockedLoadProfile()
    observability = RuntimeObservability()
    runtime = RuleRuntime(observability=observability)
    dispatcher = DeliveryDispatcher(
        provider_registry=ProviderRegistry(),
        observability=observability,
    )

    alerts: list[Alert] = []
    rule_bindings: list[RuleBinding] = []
    for i in range(cfg.active_alerts):
        alert, rule = _build_alert_and_rule(
            index=i,
            tag_buckets=cfg.tag_buckets,
        )
        alerts.append(alert)
        rule_bindings.append(
            RuleBinding(alert_id=alert.alert_id, rule=rule)
        )
    alert_by_id = {alert.alert_id: alert for alert in alerts}
    runtime.set_bindings(rule_bindings)
    bindings = [_default_binding()]

    baseline_events = cfg.baseline_eps * cfg.baseline_window_sec
    burst_events = (
        cfg.baseline_eps
        * cfg.burst_multiplier
        * cfg.burst_window_sec
    )
    total_events = baseline_events + burst_events
    decisions_emitted = 0
    dispatched_queued = 0
    started_at = perf_counter()

    for idx in range(total_events):
        _guard_runtime(
            started_at=started_at,
            max_runtime_sec=cfg.max_runtime_sec,
        )
        event = _build_event(seq=idx, tag_buckets=cfg.tag_buckets)
        _observe_ingest_lag(observability=observability, event=event)
        decisions = runtime.evaluate_event(event)
        decisions_emitted += len(decisions)
        for decision in decisions:
            alert = alert_by_id[decision.alert_id]
            stats = await dispatcher.dispatch(
                decision=decision,
                alert=alert,
                bindings=bindings,
                execute_sends=False,
            )
            dispatched_queued += stats.queued
        _maybe_log_progress(
            processed=idx + 1,
            total=total_events,
            decisions_emitted=decisions_emitted,
            queued=dispatched_queued,
            started_at=started_at,
            every_events=cfg.progress_every_events,
        )

    queued_ratio = _queued_ratio(
        queued=dispatched_queued,
        total_events=total_events,
    )
    if queued_ratio < cfg.min_queued_ratio:
        raise RuntimeError(
            (
                "queued_ratio dropped below minimum threshold: "
                f"{queued_ratio:.3f} < {cfg.min_queued_ratio:.3f}"
            )
        )
    slo = observability.check_event_to_enqueue_slo(cfg.target_p95_ms)
    return LoadHarnessResult(
        total_events=total_events,
        baseline_events=baseline_events,
        burst_events=burst_events,
        active_alerts=cfg.active_alerts,
        decisions_emitted=decisions_emitted,
        dispatched_queued=dispatched_queued,
        slo=slo,
    )


async def _run_locked_profile_dispatch_only(
    profile: LockedLoadProfile,
) -> LoadHarnessResult:
    observability = RuntimeObservability()
    dispatcher = DeliveryDispatcher(
        provider_registry=ProviderRegistry(),
        observability=observability,
    )
    alerts = [
        _build_alert_and_rule(
            index=i,
            tag_buckets=profile.tag_buckets,
        )[0]
        for i in range(profile.active_alerts)
    ]
    bindings = [_default_binding()]
    baseline_events = profile.baseline_eps * profile.baseline_window_sec
    burst_events = (
        profile.baseline_eps
        * profile.burst_multiplier
        * profile.burst_window_sec
    )
    total_events = baseline_events + burst_events
    started_at = perf_counter()
    dispatched_queued = 0
    for idx in range(total_events):
        _guard_runtime(
            started_at=started_at,
            max_runtime_sec=profile.max_runtime_sec,
        )
        alert = alerts[idx % len(alerts)]
        decision = _build_decision(seq=idx, alert=alert)
        _validate_decision_alert_invariants(decision=decision, alert=alert)
        stats = await dispatcher.dispatch(
            decision=decision,
            alert=alert,
            bindings=bindings,
            execute_sends=False,
        )
        dispatched_queued += stats.queued
        _maybe_log_progress(
            processed=idx + 1,
            total=total_events,
            decisions_emitted=idx + 1,
            queued=dispatched_queued,
            started_at=started_at,
            every_events=profile.progress_every_events,
        )
    slo = observability.check_event_to_enqueue_slo(profile.target_p95_ms)
    return LoadHarnessResult(
        total_events=total_events,
        baseline_events=baseline_events,
        burst_events=burst_events,
        active_alerts=profile.active_alerts,
        decisions_emitted=total_events,
        dispatched_queued=dispatched_queued,
        slo=slo,
    )


def _build_rule(alert_type: AlertType, bucket: int) -> AlertRuleV1:
    tag = _bucket_tag(bucket)
    rule_type = _rule_type_for_alert_type(alert_type)
    if rule_type is RuleType.TRADER_POSITION_UPDATE:
        payload = {
            "rule_id": f"load-rule-a-{bucket}",
            "tenant_id": "tenant-load",
            "name": f"load-a-{bucket}",
            "rule_type": "trader_position_update",
            "version": 1,
            "expression": {
                "signal": "PositionOpened",
                "op": "gte",
                "threshold": 1.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
            "filters": {"category_tags": [tag]},
        }
    elif rule_type is RuleType.VOLUME_SPIKE_5M:
        payload = {
            "rule_id": f"load-rule-b-{bucket}",
            "tenant_id": "tenant-load",
            "name": f"load-b-{bucket}",
            "rule_type": "volume_spike_5m",
            "version": 1,
            "expression": {
                "signal": "price_return_1m_pct",
                "op": "gte",
                "threshold": 1.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
            "filters": {"category_tags": [tag]},
        }
    else:
        payload = {
            "rule_id": f"load-rule-c-{bucket}",
            "tenant_id": "tenant-load",
            "name": f"load-c-{bucket}",
            "rule_type": "new_market_liquidity",
            "version": 1,
            "expression": {
                "signal": "liquidity_usd",
                "op": "gte",
                "threshold": 100000.0,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            },
            "filters": {"category_tags": [tag]},
            "deferred_watch": {
                "enabled": True,
                "target_liquidity_usd": 100000.0,
                "ttl_hours": 24,
            },
        }
    return AlertRuleV1.model_validate(payload)


def _build_decision(seq: int, alert: Alert) -> TriggerDecision:
    now = datetime.now(timezone.utc)
    rule_type = _rule_type_for_alert_type(alert.alert_type)
    reason = TriggerReason.model_validate(
        {
            "rule_id": alert.rule_id,
            "rule_version": alert.rule_version,
            "evaluated_at": now,
            "predicates": [],
            "summary": f"load-harness-{seq}",
        }
    )
    return TriggerDecision(
        alert_id=alert.alert_id,
        rule_id=alert.rule_id,
        rule_version=alert.rule_version,
        tenant_id="tenant-load",
        scope_id=f"m-{seq}",
        trigger_key=f"load-trigger-{seq}",
        event_ts=now,
        reason=reason,
        rule_type=rule_type.value,
        scenario=_scenario_for_rule_type(rule_type),
        source=Source.POLYMARKET.value,
        event_type=EventType.TRADE.value,
    )


def _build_alert_and_rule(
    index: int,
    tag_buckets: int,
) -> tuple[Alert, AlertRuleV1]:
    alert_type = _alert_type_for_index(index)
    bucket = index % tag_buckets
    rule = _build_rule(alert_type=alert_type, bucket=bucket)
    return (
        Alert.model_validate(
            {
                "alert_id": f"alert-{index}",
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "user_id": "u-load",
                "alert_type": alert_type,
                "filters_json": {},
                "channels": [DeliveryChannel.TELEGRAM],
                "cooldown_seconds": 0,
            }
        ),
        rule,
    )


def _build_event(seq: int, tag_buckets: int) -> CanonicalEvent:
    bucket = seq % tag_buckets
    tag = _bucket_tag(bucket)
    batch = seq // 10
    event_ts = datetime.now(timezone.utc)
    source_event_id = f"load-{seq}"
    kind = seq % 10
    if kind < 4:
        market_id = f"m-trade-{seq}"
        event_type = EventType.TRADE
        payload = {
            "price_return_1m_pct": 2.2,
            "tags": [tag],
        }
    elif kind < 8:
        market_id = f"m-position-{seq}"
        event_type = EventType.POSITION_UPDATE
        payload = {
            "action": "open",
            "smart_score": 90,
            "account_age_days": 400,
            "tags": [tag],
        }
    elif kind == 8:
        market_id = f"m-liquidity-{batch}"
        event_type = EventType.MARKET_CREATED
        payload = {"category": tag}
    else:
        market_id = f"m-liquidity-{batch}"
        event_type = EventType.LIQUIDITY_UPDATE
        payload = {
            "liquidity_usd": 120000.0,
            "category_tags": [tag],
        }
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


def _observe_ingest_lag(
    observability: RuntimeObservability,
    event: CanonicalEvent,
) -> None:
    lag_ms = max(
        0.0,
        (event.ingested_ts - event.event_ts).total_seconds() * 1000.0,
    )
    observability.observe_timing_ms(
        "ingest_lag_ms",
        lag_ms,
        labels={
            "source": event.source.value,
            "event_type": event.event_type.value,
        },
    )


def _alert_type_for_index(index: int) -> AlertType:
    mod = index % 10
    if mod < 4:
        return AlertType.VOLUME_SPIKE_5M
    if mod < 8:
        return AlertType.TRADER_POSITION_UPDATE
    return AlertType.NEW_MARKET_LIQUIDITY


def _rule_type_for_alert_type(alert_type: AlertType) -> RuleType:
    mapping = {
        AlertType.TRADER_POSITION_UPDATE: RuleType.TRADER_POSITION_UPDATE,
        AlertType.VOLUME_SPIKE_5M: RuleType.VOLUME_SPIKE_5M,
        AlertType.NEW_MARKET_LIQUIDITY: RuleType.NEW_MARKET_LIQUIDITY,
    }
    return mapping[alert_type]


def _scenario_for_rule_type(rule_type: RuleType) -> str:
    mapping = {
        RuleType.TRADER_POSITION_UPDATE: "example_a",
        RuleType.VOLUME_SPIKE_5M: "example_b",
        RuleType.NEW_MARKET_LIQUIDITY: "example_c",
    }
    return mapping[rule_type]


def _bucket_tag(bucket: int) -> str:
    return f"segment-{bucket}"


def _validate_decision_alert_invariants(
    *,
    decision: TriggerDecision,
    alert: Alert,
) -> None:
    if decision.rule_id != alert.rule_id:
        raise ValueError("decision.rule_id and alert.rule_id diverged")
    if decision.rule_version != alert.rule_version:
        raise ValueError(
            "decision.rule_version and alert.rule_version diverged"
        )


def _guard_runtime(
    *,
    started_at: float,
    max_runtime_sec: float | None,
) -> None:
    if max_runtime_sec is None:
        return
    elapsed = perf_counter() - started_at
    if elapsed > max_runtime_sec:
        raise LoadHarnessTimeoutError(
            (
                "load harness exceeded runtime budget: "
                f"{elapsed:.2f}s > {max_runtime_sec:.2f}s"
            )
        )


def _maybe_log_progress(
    *,
    processed: int,
    total: int,
    decisions_emitted: int,
    queued: int,
    started_at: float,
    every_events: int,
) -> None:
    if every_events <= 0:
        return
    if processed % every_events != 0 and processed != total:
        return
    elapsed = perf_counter() - started_at
    print(
        (
            f"[load-gate] processed={processed}/{total} "
            f"decisions={decisions_emitted} queued={queued} "
            f"elapsed_sec={elapsed:.2f}"
        ),
        flush=True,
    )


def _queued_ratio(*, queued: int, total_events: int) -> float:
    if total_events <= 0:
        return 0.0
    return queued / float(total_events)


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
