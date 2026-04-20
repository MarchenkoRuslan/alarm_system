from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from alarm_system.canonical_event import CanonicalEvent, EventType
from alarm_system.normalization import (
    extract_event_tag_ids,
    extract_event_tags,
    normalize_tag,
)
from alarm_system.rules_dsl import AlertRuleV1, RuleType


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


def _event_object_type(event_type: EventType) -> str:
    mapping = {
        EventType.TRADE: "trade",
        EventType.ORDERBOOK_DELTA: "orderbook",
        EventType.MARKET_SNAPSHOT: "market",
        EventType.MARKET_CREATED: "market",
        EventType.MARKET_RESOLVED: "market",
        EventType.LIQUIDITY_UPDATE: "market",
        EventType.POSITION_UPDATE: "position",
        EventType.WALLET_ACTIVITY: "wallet",
        EventType.METADATA_REFRESH: "market",
    }
    return mapping.get(event_type, "any")


@dataclass(frozen=True)
class RuleBinding:
    alert_id: str
    rule: AlertRuleV1
    filters_json: dict[str, str | int | float | bool | list[str]] = field(
        default_factory=dict
    )


@dataclass
class _Bucket:
    wildcard: list[RuleBinding] = field(default_factory=list)
    by_tag: dict[str, list[RuleBinding]] = field(default_factory=dict)
    by_tag_id: dict[int, list[RuleBinding]] = field(default_factory=dict)

    def add(self, binding: RuleBinding) -> None:
        tags = binding.rule.filters.category_tags
        tag_ids = _extract_rule_tag_ids(binding)
        if not tags:
            req = binding.rule.filters.require_event_tag
            if isinstance(req, str) and req.strip():
                normalized = normalize_tag(req)
                self.by_tag.setdefault(normalized, []).append(binding)
                for tag_id in tag_ids:
                    self.by_tag_id.setdefault(tag_id, []).append(binding)
                return
            if tag_ids:
                for tag_id in tag_ids:
                    self.by_tag_id.setdefault(tag_id, []).append(binding)
                return
            self.wildcard.append(binding)
            return
        for raw_tag in tags:
            normalized = normalize_tag(raw_tag)
            self.by_tag.setdefault(normalized, []).append(binding)
        for tag_id in tag_ids:
            self.by_tag_id.setdefault(tag_id, []).append(binding)


class PrefilterIndex:
    """
    Candidate index by `(object_type, field_path)` with optional tag gates.

    False-negative prevention policy:
    - if event carries no tags, return all tagged bucket candidates;
    - if event carries no field keys, fallback to wildcard field lookup;
    - if rule has neither tags nor require_event_tag, treat as wildcard.
    """

    def __init__(self) -> None:
        self._index: dict[tuple[str, str], _Bucket] = {}
        # Filled in build(): immutable totals per event_type for metrics
        # (prefilter_hit_ratio via RuntimeObservability.observe_ratio).
        self._totals_by_event_type: dict[
            EventType, dict[RuleType, int]
        ] | None = None

    def add(self, binding: RuleBinding) -> None:
        object_types = _binding_object_types(binding)
        field_paths = _binding_field_paths(binding)
        for object_type in object_types:
            for field_path in field_paths:
                key = (object_type, field_path)
                bucket = self._index.setdefault(key, _Bucket())
                bucket.add(binding)

    def build(self, bindings: Iterable[RuleBinding]) -> "PrefilterIndex":
        for binding in bindings:
            self.add(binding)
        self._totals_by_event_type = self._compute_totals_by_event_type()
        return self

    def lookup(
        self,
        event: CanonicalEvent,
        *,
        signal_keys: set[str] | None = None,
    ) -> list[RuleBinding]:
        event_tags = self._extract_event_tags(event)
        event_tag_ids = self._extract_event_tag_ids(event)
        object_type = _event_object_type(event.event_type)
        fields = self._extract_field_keys(event, signal_keys=signal_keys)
        lookup_fields = set(fields)
        if signal_keys is None:
            lookup_fields.update(
                self._all_indexed_fields_for_object_type(object_type)
            )
        lookup_fields.add("*")
        selected: dict[tuple[str, str, int], RuleBinding] = {}
        for field_path in lookup_fields:
            for key in (
                (object_type, field_path),
                (object_type, "*"),
                ("*", field_path),
                ("*", "*"),
            ):
                bucket = self._index.get(key)
                if bucket is None:
                    continue
                self._collect_bucket(
                    selected=selected,
                    bucket=bucket,
                    event_tags=event_tags,
                    event_tag_ids=event_tag_ids,
                )
        return list(selected.values())

    def _all_indexed_fields_for_object_type(self, object_type: str) -> set[str]:
        return {
            field_path
            for indexed_object_type, field_path in self._index.keys()
            if indexed_object_type in {object_type, "*"}
        }

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
        object_type = _event_object_type(event_type)
        return {
            rule_type: len(
                self._unique_bindings_for_rule_type(
                    object_type=object_type,
                    rule_type=rule_type,
                )
            )
            for rule_type in RuleType
        }

    def _unique_bindings_for_rule_type(
        self,
        *,
        object_type: str,
        rule_type: RuleType,
    ) -> set[tuple[str, str, int]]:
        unique: set[tuple[str, str, int]] = set()
        for (indexed_object_type, _field_path), bucket in self._index.items():
            if indexed_object_type not in {object_type, "*"}:
                continue
            for binding in self._iter_bucket_bindings(bucket):
                if binding.rule.rule_type is rule_type:
                    unique.add(self._binding_identity(binding))
        return unique

    @staticmethod
    def _remember(
        selected: dict[tuple[str, str, int], RuleBinding],
        binding: RuleBinding,
    ) -> None:
        selected[PrefilterIndex._binding_identity(binding)] = binding

    @staticmethod
    def _binding_identity(binding: RuleBinding) -> tuple[str, str, int]:
        return (binding.alert_id, binding.rule.rule_id, binding.rule.version)

    @staticmethod
    def _iter_bucket_bindings(bucket: _Bucket) -> Iterable[RuleBinding]:
        yield from bucket.wildcard
        for tagged_bindings in bucket.by_tag.values():
            yield from tagged_bindings
        for tagged_bindings in bucket.by_tag_id.values():
            yield from tagged_bindings

    def _collect_bucket(
        self,
        *,
        selected: dict[tuple[str, str, int], RuleBinding],
        bucket: _Bucket,
        event_tags: list[str],
        event_tag_ids: list[int],
    ) -> None:
        self._remember_many(selected, bucket.wildcard)
        if not event_tags and not event_tag_ids:
            self._remember_tag_index(selected, bucket.by_tag.values())
            self._remember_tag_index(selected, bucket.by_tag_id.values())
            return
        self._remember_index_hits(selected, bucket.by_tag, event_tags)
        self._remember_index_hits(selected, bucket.by_tag_id, event_tag_ids)

    def _remember_many(
        self,
        selected: dict[tuple[str, str, int], RuleBinding],
        bindings: Iterable[RuleBinding],
    ) -> None:
        for binding in bindings:
            self._remember(selected, binding)

    def _remember_tag_index(
        self,
        selected: dict[tuple[str, str, int], RuleBinding],
        values: Iterable[list[RuleBinding]],
    ) -> None:
        for bindings in values:
            self._remember_many(selected, bindings)

    def _remember_index_hits(
        self,
        selected: dict[tuple[str, str, int], RuleBinding],
        index: dict[str, list[RuleBinding]] | dict[int, list[RuleBinding]],
        keys: Iterable[str] | Iterable[int],
    ) -> None:
        for key in keys:
            self._remember_many(selected, index.get(key, []))

    @staticmethod
    def _extract_field_keys(
        event: CanonicalEvent,
        *,
        signal_keys: set[str] | None,
    ) -> set[str]:
        if signal_keys:
            return {key for key in signal_keys if key}
        return {
            key for key in event.payload.keys() if isinstance(key, str) and key
        }

    @staticmethod
    def _extract_event_tags(event: CanonicalEvent) -> list[str]:
        return extract_event_tags(event.payload)

    @staticmethod
    def _extract_event_tag_ids(event: CanonicalEvent) -> list[int]:
        return extract_event_tag_ids(event.payload)


def _binding_object_types(binding: RuleBinding) -> list[str]:
    if binding.rule.object_types:
        return sorted({_normalize_obj(item) for item in binding.rule.object_types})
    object_types = {
        _event_object_type(event_type)
        for event_type in _rule_event_types(binding.rule.rule_type)
    }
    if not object_types:
        return ["*"]
    return sorted(object_types)


def _binding_field_paths(binding: RuleBinding) -> list[str]:
    paths = [path.strip() for path in binding.rule.field_paths if path.strip()]
    if not paths:
        return ["*"]
    if binding.rule.rule_type is RuleType.NEW_MARKET_LIQUIDITY:
        # NEW_MARKET_LIQUIDITY needs MARKET_CREATED events even when expression
        # signals are absent in payload; wildcard keeps deferred-watch arming.
        return sorted(set(paths) | {"*"})
    return sorted(set(paths))


def _extract_rule_tag_ids(binding: RuleBinding) -> list[int]:
    tag_ids_raw = binding.filters_json.get("tag_ids")
    if not isinstance(tag_ids_raw, list):
        return []
    tag_ids: set[int] = set()
    for value in tag_ids_raw:
        if isinstance(value, int):
            tag_ids.add(value)
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                tag_ids.add(int(stripped))
    return sorted(tag_ids)


def _normalize_obj(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized else "*"
