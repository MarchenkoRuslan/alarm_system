from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from alarm_system.canonical_event import CanonicalEvent, EventType
from alarm_system.rules_dsl import AlertRuleV1, RuleType


def _normalize_tag(tag: str) -> str:
    return tag.strip().lower()


def _rule_event_types(rule_type: RuleType) -> tuple[EventType, ...]:
    mapping = {
        RuleType.TRADER_POSITION_UPDATE: (EventType.POSITION_UPDATE,),
        RuleType.VOLUME_SPIKE_5M: (
            EventType.TRADE,
            EventType.ORDERBOOK_DELTA,
            EventType.MARKET_SNAPSHOT,
            EventType.LIQUIDITY_UPDATE,
        ),
        RuleType.NEW_MARKET_LIQUIDITY: (
            EventType.MARKET_CREATED,
            EventType.LIQUIDITY_UPDATE,
        ),
    }
    return mapping[rule_type]


@dataclass(frozen=True)
class RuleBinding:
    alert_id: str
    rule: AlertRuleV1


@dataclass
class _Bucket:
    wildcard: list[RuleBinding] = field(default_factory=list)
    by_tag: dict[str, list[RuleBinding]] = field(default_factory=dict)

    def add(self, binding: RuleBinding) -> None:
        tags = binding.rule.filters.category_tags
        if not tags:
            self.wildcard.append(binding)
            return
        for raw_tag in tags:
            normalized = _normalize_tag(raw_tag)
            self.by_tag.setdefault(normalized, []).append(binding)


class PrefilterIndex:
    """
    Coarse candidate index by `(rule_type, tag, event_type)`.

    False-negative prevention policy:
    - if event carries no tags, return all bucket rules for that `(rule_type, event_type)`;
    - if rule has no category tags, treat it as wildcard.
    """

    def __init__(self) -> None:
        self._index: dict[tuple[RuleType, EventType], _Bucket] = {}
        # Filled in build(): immutable totals per event_type for metrics (prefilter_hit_ratio).
        self._totals_by_event_type: dict[
            EventType, dict[RuleType, int]
        ] | None = None

    def add(self, binding: RuleBinding) -> None:
        for event_type in _rule_event_types(binding.rule.rule_type):
            key = (binding.rule.rule_type, event_type)
            bucket = self._index.setdefault(key, _Bucket())
            bucket.add(binding)

    def build(self, bindings: Iterable[RuleBinding]) -> "PrefilterIndex":
        for binding in bindings:
            self.add(binding)
        self._totals_by_event_type = self._compute_totals_by_event_type()
        return self

    def lookup(self, event: CanonicalEvent) -> list[RuleBinding]:
        event_tags = self._extract_event_tags(event)
        selected: dict[tuple[str, str, int], RuleBinding] = {}
        for rule_type in RuleType:
            bucket = self._index.get((rule_type, event.event_type))
            if bucket is None:
                continue
            for binding in bucket.wildcard:
                self._remember(selected, binding)
            if not event_tags:
                for tagged_bindings in bucket.by_tag.values():
                    for binding in tagged_bindings:
                        self._remember(selected, binding)
                continue
            for tag in event_tags:
                for binding in bucket.by_tag.get(tag, []):
                    self._remember(selected, binding)
        return list(selected.values())

    def total_bindings_for_event(
        self,
        event_type: EventType,
    ) -> dict[RuleType, int]:
        if self._totals_by_event_type is not None:
            return self._totals_by_event_type.get(event_type, {})
        return self._totals_for_event_type_uncached(event_type)

    def _compute_totals_by_event_type(
        self,
    ) -> dict[EventType, dict[RuleType, int]]:
        return {
            event_type: self._totals_for_event_type_uncached(event_type)
            for event_type in EventType
        }

    def _totals_for_event_type_uncached(
        self,
        event_type: EventType,
    ) -> dict[RuleType, int]:
        totals: dict[RuleType, int] = {}
        for rule_type in RuleType:
            bucket = self._index.get((rule_type, event_type))
            if bucket is None:
                continue
            unique: set[tuple[str, str, int]] = set()
            for binding in bucket.wildcard:
                unique.add(
                    (
                        binding.alert_id,
                        binding.rule.rule_id,
                        binding.rule.version,
                    )
                )
            for tagged_bindings in bucket.by_tag.values():
                for binding in tagged_bindings:
                    unique.add(
                        (
                            binding.alert_id,
                            binding.rule.rule_id,
                            binding.rule.version,
                        )
                    )
            totals[rule_type] = len(unique)
        return totals

    @staticmethod
    def _remember(
        selected: dict[tuple[str, str, int], RuleBinding],
        binding: RuleBinding,
    ) -> None:
        dedup_key = (binding.alert_id, binding.rule.rule_id, binding.rule.version)
        selected[dedup_key] = binding

    @staticmethod
    def _extract_event_tags(event: CanonicalEvent) -> list[str]:
        payload = event.payload
        tags = payload.get("tags")
        if isinstance(tags, list):
            result = []
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    result.append(_normalize_tag(tag))
                elif isinstance(tag, dict):
                    label = tag.get("label") or tag.get("name")
                    if isinstance(label, str) and label.strip():
                        result.append(_normalize_tag(label))
            if result:
                return sorted(set(result))
        category = payload.get("category")
        if isinstance(category, str) and category.strip():
            return [_normalize_tag(category)]
        category_tags = payload.get("category_tags")
        if isinstance(category_tags, list):
            result = [
                _normalize_tag(tag)
                for tag in category_tags
                if isinstance(tag, str) and tag.strip()
            ]
            if result:
                return sorted(set(result))
        return []
