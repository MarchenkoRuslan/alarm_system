from __future__ import annotations

from dataclasses import dataclass

from alarm_system.canonical_event import CanonicalEvent, EventType
from alarm_system.compute.features import extract_feature_snapshot
from alarm_system.compute.prefilter import PrefilterIndex, RuleBinding
from alarm_system.rules.deferred_watch import InMemoryDeferredWatchStore
from alarm_system.rules.evaluator import RuleEvaluator
from alarm_system.rules_dsl import RuleType, TriggerReason


@dataclass(frozen=True)
class TriggerDecision:
    alert_id: str
    rule_id: str
    rule_version: int
    scope_id: str
    reason: TriggerReason


class RuleRuntime:
    def __init__(
        self,
        prefilter: PrefilterIndex | None = None,
        evaluator: RuleEvaluator | None = None,
        deferred_watches: InMemoryDeferredWatchStore | None = None,
    ) -> None:
        self._prefilter = prefilter or PrefilterIndex()
        self._evaluator = evaluator or RuleEvaluator()
        self._deferred_watches = deferred_watches or InMemoryDeferredWatchStore()
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
            raise RuntimeError("Rule bindings are not loaded. Call set_bindings() first.")
        candidates = self._prefilter.lookup(event)
        features = extract_feature_snapshot(event)
        decisions: list[TriggerDecision] = []
        for binding in candidates:
            rule = binding.rule
            event_tags = set(features.tags)
            rule_tags = {tag.strip().lower() for tag in rule.filters.category_tags}
            tag_match = self._tags_match(rule_tags=rule_tags, event_tags=event_tags)
            if not tag_match:
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
                fired = self._deferred_watches.check_and_fire(
                    alert_id=binding.alert_id,
                    market_id=event.market_ref.market_id,
                    liquidity_usd=liquidity_usd,
                    at=event.event_ts,
                )
                if not fired:
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
            decisions.append(
                TriggerDecision(
                    alert_id=binding.alert_id,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    scope_id=event.market_ref.market_id,
                    reason=evaluation.reason,
                )
            )
        return decisions

    @staticmethod
    def _tags_match(rule_tags: set[str], event_tags: set[str]) -> bool:
        if not rule_tags:
            return True
        if not event_tags:
            return False
        return bool(rule_tags.intersection(event_tags))
