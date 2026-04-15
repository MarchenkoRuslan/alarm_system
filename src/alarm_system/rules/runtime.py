from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from alarm_system.canonical_event import CanonicalEvent, EventType
from alarm_system.compute.features import extract_feature_snapshot
from alarm_system.compute.prefilter import PrefilterIndex, RuleBinding
from alarm_system.rules.deferred_watch import InMemoryDeferredWatchStore
from alarm_system.rules.evaluator import RuleEvaluator
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

    def set_bindings(self, bindings: list[RuleBinding]) -> None:
        self._prefilter = PrefilterIndex().build(bindings)
        self._bindings_loaded = True

    def load_bindings(self, bindings: list[RuleBinding]) -> None:
        self.set_bindings(bindings)

    def evaluate_event(
        self,
        event: CanonicalEvent,
    ) -> list[TriggerDecision]:
        if not self._bindings_loaded:
            raise RuntimeError(
                "Rule bindings are not loaded. Call set_bindings() first."
            )
        candidates = self._prefilter.lookup(event)
        features = extract_feature_snapshot(event)
        decisions: list[TriggerDecision] = []
        for binding in candidates:
            rule = binding.rule
            event_tags = set(features.tags)
            rule_tags = {
                tag.strip().lower() for tag in rule.filters.category_tags
            }
            tag_match = self._tags_match(rule_tags=rule_tags, event_tags=event_tags)
            if not tag_match:
                continue
            if not self._passes_non_tag_filters(
                binding=binding,
                signal_values=features.values,
                event_tags=event_tags,
            ):
                continue

            if rule.rule_type is RuleType.NEW_MARKET_LIQUIDITY:
                if event.event_type is EventType.MARKET_CREATED:
                    self._deferred_watches.arm(
                        alert_id=binding.alert_id,
                        market_id=event.market_ref.market_id,
                        rule=rule,
                        armed_at=event.event_ts,
                    )
                    continue
                if event.event_type is not EventType.LIQUIDITY_UPDATE:
                    continue
                liquidity_usd = features.values.get("liquidity_usd")
                if liquidity_usd is None:
                    continue
                crossed = self._deferred_watches.is_crossed(
                    alert_id=binding.alert_id,
                    market_id=event.market_ref.market_id,
                    liquidity_usd=liquidity_usd,
                    at=event.event_ts,
                )
                if not crossed:
                    continue

            matched_filters = {}
            if rule.filters.category_tags and features.tags:
                matched = sorted(rule_tags.intersection(event_tags))
                if matched:
                    matched_filters["category_tags"] = ",".join(matched)
            evaluation = self._evaluator.evaluate(
                rule=rule,
                signal_values=features.values,
                matched_filters=matched_filters,
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
            reserve_ttl = (
                self._dedup_bucket_seconds
                + self._dedup_safety_margin_seconds
            )
            should_emit, trigger_key = self._dedup.reserve(
                tenant_id=rule.tenant_id,
                rule_id=rule.rule_id,
                rule_version=rule.version,
                scope_id=event.market_ref.market_id,
                event_time=event.event_ts,
                bucket_seconds=self._dedup_bucket_seconds,
                ttl_seconds=reserve_ttl,
            )
            if not should_emit:
                continue
            if rule.rule_type is RuleType.NEW_MARKET_LIQUIDITY:
                fired = self._deferred_watches.mark_fired(
                    alert_id=binding.alert_id,
                    market_id=event.market_ref.market_id,
                    fired_at=event.event_ts,
                )
                if not fired:
                    continue
            decisions.append(
                TriggerDecision(
                    alert_id=binding.alert_id,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    tenant_id=rule.tenant_id,
                    scope_id=event.market_ref.market_id,
                    trigger_key=trigger_key,
                    event_ts=event.event_ts,
                    reason=evaluation.reason,
                )
            )
        return decisions

    @staticmethod
    def _passes_non_tag_filters(
        binding: RuleBinding,
        signal_values: dict[str, float],
        event_tags: set[str],
    ) -> bool:
        filters = binding.rule.filters
        if filters.iran_tag_only and "iran" not in event_tags:
            return False
        if filters.min_smart_score is not None:
            smart_score = signal_values.get("smart_score")
            if smart_score is None or smart_score < filters.min_smart_score:
                return False
        if filters.min_account_age_days is not None:
            account_age_days = signal_values.get("account_age_days")
            if (
                account_age_days is None
                or account_age_days < float(filters.min_account_age_days)
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
