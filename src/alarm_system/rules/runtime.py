from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Protocol

from alarm_system.alert_filters import (
    effective_min_account_age_days,
    effective_min_smart_score,
    effective_require_event_tag,
    matched_filter_evidence,
    passes_alert_filters,
)
from alarm_system.canonical_event import CanonicalEvent, EventType
from alarm_system.compute.features import extract_feature_snapshot
from alarm_system.observability import RuntimeObservability
from alarm_system.compute.prefilter import PrefilterIndex, RuleBinding
from alarm_system.rules.deferred_watch import InMemoryDeferredWatchStore
from alarm_system.rules.evaluator import EvaluationResult, RuleEvaluator
from alarm_system.rules.suppression import InMemorySuppressionStore
from alarm_system.rules_dsl import AlertRuleV1, RuleType, TriggerReason
from alarm_system.state import (
    InMemoryTriggerDedupStore,
    TriggerDedupStore,
)


class DeferredWatchStore(Protocol):
    def arm(
        self,
        alert_id: str,
        market_id: str,
        rule: AlertRuleV1,
        armed_at: datetime,
        filters_json: dict[str, str | int | float | bool | list[str]] | None = None,
    ) -> bool:
        ...

    def check_and_fire(
        self,
        alert_id: str,
        market_id: str,
        liquidity_usd: float,
        at: datetime,
    ) -> bool:
        ...

    def is_crossed(
        self,
        alert_id: str,
        market_id: str,
        liquidity_usd: float,
        at: datetime,
    ) -> bool:
        ...

    def mark_fired(
        self,
        alert_id: str,
        market_id: str,
        fired_at: datetime,
    ) -> bool:
        ...


class SuppressionStore(Protocol):
    def should_suppress(
        self,
        alert_id: str,
        scope_id: str,
        rule: AlertRuleV1,
        signal_values: dict[str, float],
        at: datetime,
    ) -> bool:
        ...


@dataclass(frozen=True)
class TriggerDecision:
    alert_id: str
    rule_id: str
    rule_version: int
    tenant_id: str
    scope_id: str
    trigger_key: str
    event_ts: datetime
    reason: TriggerReason
    rule_type: str | None = None
    scenario: str | None = None
    source: str | None = None
    event_type: str | None = None


class RuleRuntime:
    def __init__(
        self,
        prefilter: PrefilterIndex | None = None,
        evaluator: RuleEvaluator | None = None,
        deferred_watches: DeferredWatchStore | None = None,
        suppression: SuppressionStore | None = None,
        dedup: TriggerDedupStore | None = None,
        dedup_bucket_seconds: int = 60,
        dedup_safety_margin_seconds: int = 5,
        observability: RuntimeObservability | None = None,
    ) -> None:
        self._prefilter = prefilter or PrefilterIndex()
        self._evaluator = evaluator or RuleEvaluator()
        self._deferred_watches = (
            deferred_watches or InMemoryDeferredWatchStore()
        )
        self._suppression = suppression or InMemorySuppressionStore()
        self._dedup = dedup or InMemoryTriggerDedupStore()
        self._dedup_bucket_seconds = dedup_bucket_seconds
        self._dedup_safety_margin_seconds = dedup_safety_margin_seconds
        self._bindings_loaded = prefilter is not None
        self._observability = observability

    def set_bindings(self, bindings: list[RuleBinding]) -> None:
        self._prefilter = PrefilterIndex().build(bindings)
        self._bindings_loaded = True

    def evaluate_event(
        self,
        event: CanonicalEvent,
    ) -> list[TriggerDecision]:
        self._ensure_bindings_loaded()
        candidates = self._prefilter.lookup(event)
        self._observe_prefilter_hit_ratio(
            event=event,
            candidates=candidates,
        )
        features = extract_feature_snapshot(event)
        event_tags = set(features.tags)
        decisions: list[TriggerDecision] = []
        for binding in candidates:
            rule = binding.rule
            rule_tags = self._normalize_rule_tags(rule)
            if not self._candidate_matches(
                binding=binding,
                rule_tags=rule_tags,
                event_tags=event_tags,
                signal_values=features.values,
            ):
                continue

            if self._skip_deferred_watch_preconditions(
                binding=binding,
                event=event,
                signal_values=features.values,
            ):
                continue

            evaluation = self._evaluate_rule(
                binding=binding,
                signal_values=features.values,
                rule_tags=rule_tags,
                event_tags=event_tags,
                evaluated_at=event.event_ts,
            )
            if not evaluation.triggered:
                continue

            if self._suppression.should_suppress(
                alert_id=binding.alert_id,
                scope_id=event.market_ref.market_id,
                rule=rule,
                signal_values=features.values,
                at=event.event_ts,
            ):
                continue

            should_emit, trigger_key = self._reserve_trigger(
                rule=rule,
                scope_id=event.market_ref.market_id,
                event_ts=event.event_ts,
            )
            if not should_emit:
                self._record_dedup_hit(rule.rule_type)
                continue

            if not self._mark_watch_fired_if_needed(
                binding=binding, event=event
            ):
                continue

            decisions.append(
                self._build_decision(
                    binding=binding,
                    event=event,
                    trigger_key=trigger_key,
                    reason=evaluation.reason,
                )
            )
        return decisions

    def _ensure_bindings_loaded(self) -> None:
        if not self._bindings_loaded:
            raise RuntimeError(
                "Rule bindings are not loaded. Call set_bindings() first."
            )

    @staticmethod
    def _normalize_rule_tags(rule: AlertRuleV1) -> set[str]:
        return {tag.strip().lower() for tag in rule.filters.category_tags}

    def _candidate_matches(
        self,
        *,
        binding: RuleBinding,
        rule_tags: set[str],
        event_tags: set[str],
        signal_values: dict[str, float],
    ) -> bool:
        return self._tags_match(
            rule_tags=rule_tags,
            event_tags=event_tags,
        ) and self._passes_non_tag_filters(
            binding=binding,
            signal_values=signal_values,
            event_tags=event_tags,
        ) and passes_alert_filters(
            binding.filters_json,
            signal_values=signal_values,
            event_tags=event_tags,
        )

    def _skip_deferred_watch_preconditions(
        self,
        *,
        binding: RuleBinding,
        event: CanonicalEvent,
        signal_values: dict[str, float],
    ) -> bool:
        rule = binding.rule
        if rule.rule_type is not RuleType.NEW_MARKET_LIQUIDITY:
            return False
        if event.event_type is EventType.MARKET_CREATED:
            self._deferred_watches.arm(
                alert_id=binding.alert_id,
                market_id=event.market_ref.market_id,
                rule=rule,
                armed_at=event.event_ts,
                filters_json=binding.filters_json,
            )
            return True
        if event.event_type is not EventType.LIQUIDITY_UPDATE:
            return True
        liquidity_usd = signal_values.get("liquidity_usd")
        if liquidity_usd is None:
            return True
        crossed = self._deferred_watches.is_crossed(
            alert_id=binding.alert_id,
            market_id=event.market_ref.market_id,
            liquidity_usd=liquidity_usd,
            at=event.event_ts,
        )
        return not crossed

    def _evaluate_rule(
        self,
        *,
        binding: RuleBinding,
        signal_values: dict[str, float],
        rule_tags: set[str],
        event_tags: set[str],
        evaluated_at: datetime,
    ) -> EvaluationResult:
        matched_filters = self._build_matched_filters(
            binding=binding,
            rule_tags=rule_tags,
            event_tags=event_tags,
            signal_values=signal_values,
        )
        started = perf_counter()
        evaluation = self._evaluator.evaluate(
            rule=binding.rule,
            signal_values=signal_values,
            matched_filters=matched_filters,
            evaluated_at=evaluated_at,
        )
        self._observe_rule_eval(
            rule_type=binding.rule.rule_type,
            elapsed_ms=(perf_counter() - started) * 1000.0,
        )
        return evaluation

    @staticmethod
    def _build_matched_filters(
        *,
        binding: RuleBinding,
        rule_tags: set[str],
        event_tags: set[str],
        signal_values: dict[str, float],
    ) -> dict[str, str]:
        return matched_filter_evidence(
            binding.rule,
            dict(binding.filters_json) if binding.filters_json else {},
            rule_tags=rule_tags,
            event_tags=event_tags,
            signal_values=signal_values,
        )

    def _observe_rule_eval(self, *, rule_type: RuleType, elapsed_ms: float) -> None:
        if self._observability is None:
            return
        self._observability.observe_timing_ms(
            "rule_eval_ms",
            elapsed_ms,
            labels={
                "rule_type": rule_type.value,
                "scenario": _scenario_for_rule_type(rule_type),
            },
        )

    def _reserve_trigger(
        self,
        *,
        rule: AlertRuleV1,
        scope_id: str,
        event_ts: datetime,
    ) -> tuple[bool, str]:
        reserve_ttl = (
            self._dedup_bucket_seconds + self._dedup_safety_margin_seconds
        )
        return self._dedup.reserve(
            tenant_id=rule.tenant_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            scope_id=scope_id,
            event_time=event_ts,
            bucket_seconds=self._dedup_bucket_seconds,
            ttl_seconds=reserve_ttl,
        )

    def _record_dedup_hit(self, rule_type: RuleType) -> None:
        if self._observability is None:
            return
        self._observability.increment(
            "dedup_hits_total",
            labels={
                "rule_type": rule_type.value,
                "scenario": _scenario_for_rule_type(rule_type),
                "channel": "any",
            },
        )

    def _mark_watch_fired_if_needed(
        self, *, binding: RuleBinding, event: CanonicalEvent
    ) -> bool:
        if binding.rule.rule_type is not RuleType.NEW_MARKET_LIQUIDITY:
            return True
        return self._deferred_watches.mark_fired(
            alert_id=binding.alert_id,
            market_id=event.market_ref.market_id,
            fired_at=event.event_ts,
        )

    @staticmethod
    def _build_decision(
        *,
        binding: RuleBinding,
        event: CanonicalEvent,
        trigger_key: str,
        reason: TriggerReason,
    ) -> TriggerDecision:
        rule = binding.rule
        return TriggerDecision(
            alert_id=binding.alert_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            tenant_id=rule.tenant_id,
            scope_id=event.market_ref.market_id,
            trigger_key=trigger_key,
            event_ts=event.event_ts,
            reason=reason,
            rule_type=rule.rule_type.value,
            scenario=_scenario_for_rule_type(rule.rule_type),
            source=event.source.value,
            event_type=event.event_type.value,
        )

    def _observe_prefilter_hit_ratio(
        self,
        *,
        event: CanonicalEvent,
        candidates: list[RuleBinding],
    ) -> None:
        if self._observability is None:
            return
        totals_by_type = self._prefilter.total_bindings_for_event(
            event.event_type
        )
        candidate_counts: dict[RuleType, int] = {}
        for binding in candidates:
            rule_type = binding.rule.rule_type
            candidate_counts[rule_type] = (
                candidate_counts.get(rule_type, 0) + 1
            )
        for rule_type, total in totals_by_type.items():
            if total <= 0:
                continue
            ratio = candidate_counts.get(rule_type, 0) / float(total)
            self._observability.observe_ratio(
                "prefilter_hit_ratio",
                ratio,
                labels={
                    "rule_type": rule_type.value,
                    "scenario": _scenario_for_rule_type(rule_type),
                },
            )

    @staticmethod
    def _passes_non_tag_filters(
        binding: RuleBinding,
        signal_values: dict[str, float],
        event_tags: set[str],
    ) -> bool:
        filters = binding.rule.filters
        fj = dict(binding.filters_json) if binding.filters_json else {}
        req_tag = effective_require_event_tag(filters, fj)
        if req_tag is not None and req_tag not in event_tags:
            return False
        min_smart = effective_min_smart_score(filters, fj)
        if min_smart is not None:
            smart_score = signal_values.get("smart_score")
            if smart_score is None or smart_score < min_smart:
                return False
        min_age = effective_min_account_age_days(filters, fj)
        if min_age is not None:
            account_age_days = signal_values.get("account_age_days")
            if (
                account_age_days is None
                or account_age_days < float(min_age)
            ):
                return False
        return True

    @staticmethod
    def _tags_match(rule_tags: set[str], event_tags: set[str]) -> bool:
        if not rule_tags:
            return True
        if not event_tags:
            return False
        return bool(rule_tags.intersection(event_tags))


def _scenario_for_rule_type(rule_type: RuleType) -> str:
    mapping = {
        RuleType.TRADER_POSITION_UPDATE: "example_a",
        RuleType.VOLUME_SPIKE_5M: "example_b",
        RuleType.NEW_MARKET_LIQUIDITY: "example_c",
    }
    return mapping[rule_type]
