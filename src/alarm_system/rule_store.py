from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from alarm_system.rules_dsl import (
    AlertRuleV1,
    BoolOp,
    CompareOp,
    Condition,
    DeferredWatchConfig,
    Expression,
    Group,
    RuleFilters,
    Window,
)


class RuleStoreBackendError(RuntimeError):
    """Raised when Postgres rule store is unavailable or misconfigured."""


class RuleStoreContractError(ValueError):
    """Raised when rules in storage violate required structural invariants."""


@dataclass(frozen=True)
class RuleSnapshot:
    version: int
    rules: list[AlertRuleV1]


class RuleStore(Protocol):
    def get_active_snapshot(self) -> RuleSnapshot:
        ...

    def get_active_version(self) -> int | None:
        ...


@dataclass(frozen=True)
class _RuleRow:
    rule_pk: int
    rule_id: str
    version: int
    tenant_id: str
    name: str
    rule_type: str
    object_type: str
    severity: str
    cooldown_seconds: int
    deferred_watch_json: dict[str, Any]


@dataclass(frozen=True)
class _GroupRow:
    group_id: int
    parent_group_id: int | None
    bool_op: str
    position: int


@dataclass(frozen=True)
class _PredicateRow:
    group_id: int
    position: int
    field_path: str
    comparator: str
    operand_json: Any
    window_size_seconds: int
    window_slide_seconds: int
    market_scope: str


class PostgresRuleStore(RuleStore):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'psycopg' package is required for Postgres rule store."
            ) from exc
        return psycopg.connect(self._dsn)

    def get_active_version(self) -> int | None:
        query = (
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise _to_rule_backend_error(exc, operation="read active version") from exc
        if not rows:
            return None
        if len(rows) > 1:
            raise RuleStoreContractError(
                "Exactly one active rule_set is required; found multiple."
            )
        return int(rows[0][1])

    def get_active_snapshot(self) -> RuleSnapshot:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                active_set = self._load_active_rule_set(cur)
                if active_set is None:
                    return RuleSnapshot(version=0, rules=[])
                rule_set_id, set_version = active_set
                rule_rows = self._load_rule_rows(cur, rule_set_id=rule_set_id)
                if not rule_rows:
                    return RuleSnapshot(version=set_version, rules=[])
                rule_pk_list = [row.rule_pk for row in rule_rows]
                groups_by_rule = self._load_groups_by_rule(cur, rule_pk_list=rule_pk_list)
                predicates_by_group = self._load_predicates_by_group(
                    cur,
                    rule_pk_list=rule_pk_list,
                )
                tags_by_rule_pk = self._load_required_tags_by_rule(
                    cur,
                    rule_pk_list=rule_pk_list,
                )
                object_types_by_rule_pk, field_paths_by_rule_pk = (
                    self._load_field_indexes_by_rule(
                        cur,
                        rule_pk_list=rule_pk_list,
                    )
                )
        except RuleStoreContractError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _to_rule_backend_error(exc, operation="read active rules snapshot") from exc

        materialized: list[AlertRuleV1] = []
        for row in rule_rows:
            expression = _build_expression(
                rule_pk=row.rule_pk,
                groups=groups_by_rule.get(row.rule_pk, []),
                predicates_by_group=predicates_by_group,
            )
            rule = AlertRuleV1.model_validate(
                {
                    "rule_id": row.rule_id,
                    "tenant_id": row.tenant_id,
                    "name": row.name,
                    "rule_type": row.rule_type,
                    "severity": row.severity,
                    "object_types": sorted(
                        object_types_by_rule_pk.get(row.rule_pk, {row.object_type})
                    ),
                    "expression": expression.model_dump(mode="json"),
                    "field_paths": sorted(
                        field_paths_by_rule_pk.get(
                            row.rule_pk,
                            _collect_expression_signals(expression),
                        )
                    ),
                    "cooldown_seconds": row.cooldown_seconds,
                    "filters": RuleFilters(
                        category_tags=sorted(tags_by_rule_pk.get(row.rule_pk, []))
                    ).model_dump(mode="json"),
                    "deferred_watch": DeferredWatchConfig.model_validate(
                        row.deferred_watch_json or {}
                    ).model_dump(mode="json"),
                    "version": row.version,
                }
            )
            materialized.append(rule)

        return RuleSnapshot(version=set_version, rules=materialized)

    def _load_active_rule_set(self, cur: Any) -> tuple[int, int] | None:
        cur.execute(
            "SELECT rule_set_id, version "
            "FROM rule_sets "
            "WHERE status = 'active' "
            "ORDER BY activated_at DESC NULLS LAST, version DESC "
            "LIMIT 2"
        )
        rows = cur.fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise RuleStoreContractError(
                "Exactly one active rule_set is required; found multiple."
            )
        return int(rows[0][0]), int(rows[0][1])

    def _load_rule_rows(self, cur: Any, *, rule_set_id: int) -> list[_RuleRow]:
        cur.execute(
            "SELECT "
            "r.rule_pk, r.rule_id, r.version, r.tenant_id, r.name, "
            "r.rule_type, r.object_type, r.severity, r.cooldown_seconds, r.deferred_watch_json "
            "FROM rules r "
            "WHERE r.rule_set_id = %s AND r.enabled = true "
            "ORDER BY r.rule_id ASC, r.version ASC",
            (rule_set_id,),
        )
        return [
            _RuleRow(
                rule_pk=int(row[0]),
                rule_id=str(row[1]),
                version=int(row[2]),
                tenant_id=str(row[3]),
                name=str(row[4]),
                rule_type=str(row[5]),
                object_type=str(row[6]),
                severity=str(row[7]),
                cooldown_seconds=int(row[8]),
                deferred_watch_json=_to_dict(row[9]),
            )
            for row in cur.fetchall()
        ]

    def _load_groups_by_rule(
        self,
        cur: Any,
        *,
        rule_pk_list: list[int],
    ) -> dict[int, list[_GroupRow]]:
        cur.execute(
            "SELECT g.group_id, g.rule_pk, g.parent_group_id, g.bool_op, g.position "
            "FROM rule_groups g "
            "WHERE g.rule_pk = ANY(%s) "
            "ORDER BY g.rule_pk ASC, g.parent_group_id ASC NULLS FIRST, g.position ASC",
            (rule_pk_list,),
        )
        groups_by_rule: dict[int, list[_GroupRow]] = {}
        for row in cur.fetchall():
            rule_pk = int(row[1])
            groups_by_rule.setdefault(rule_pk, []).append(
                _GroupRow(
                    group_id=int(row[0]),
                    parent_group_id=int(row[2]) if row[2] is not None else None,
                    bool_op=str(row[3]),
                    position=int(row[4]),
                )
            )
        return groups_by_rule

    def _load_predicates_by_group(
        self,
        cur: Any,
        *,
        rule_pk_list: list[int],
    ) -> dict[int, list[_PredicateRow]]:
        cur.execute(
            "SELECT p.group_id, p.position, p.field_path, p.comparator, p.operand_json, "
            "p.window_size_seconds, p.window_slide_seconds, p.market_scope "
            "FROM rule_predicates p "
            "JOIN rule_groups g ON g.group_id = p.group_id "
            "WHERE g.rule_pk = ANY(%s) "
            "ORDER BY p.group_id ASC, p.position ASC",
            (rule_pk_list,),
        )
        predicates_by_group: dict[int, list[_PredicateRow]] = {}
        for row in cur.fetchall():
            group_id = int(row[0])
            predicates_by_group.setdefault(group_id, []).append(
                _PredicateRow(
                    group_id=group_id,
                    position=int(row[1]),
                    field_path=str(row[2]),
                    comparator=str(row[3]),
                    operand_json=row[4],
                    window_size_seconds=int(row[5]),
                    window_slide_seconds=int(row[6]),
                    market_scope=str(row[7]),
                )
            )
        return predicates_by_group

    def _load_required_tags_by_rule(
        self,
        cur: Any,
        *,
        rule_pk_list: list[int],
    ) -> dict[int, list[str]]:
        cur.execute(
            "SELECT rt.rule_pk, t.normalized_label, rt.required "
            "FROM rule_tags rt "
            "JOIN tags t ON t.tag_id = rt.tag_id "
            "WHERE rt.rule_pk = ANY(%s)",
            (rule_pk_list,),
        )
        tags_by_rule_pk: dict[int, list[str]] = {}
        for row in cur.fetchall():
            rule_pk = int(row[0])
            label = str(row[1]).strip().lower()
            required = bool(row[2])
            if not required:
                continue
            tags = tags_by_rule_pk.setdefault(rule_pk, [])
            if label and label not in tags:
                tags.append(label)
        return tags_by_rule_pk

    def _load_field_indexes_by_rule(
        self,
        cur: Any,
        *,
        rule_pk_list: list[int],
    ) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
        cur.execute(
            "SELECT i.rule_pk, i.object_type, i.field_path "
            "FROM rule_object_field_index i "
            "WHERE i.rule_pk = ANY(%s)",
            (rule_pk_list,),
        )
        object_types_by_rule_pk: dict[int, set[str]] = {}
        field_paths_by_rule_pk: dict[int, set[str]] = {}
        for row in cur.fetchall():
            rule_pk = int(row[0])
            object_type = str(row[1]).strip().lower()
            field_path = str(row[2]).strip()
            if object_type:
                object_types_by_rule_pk.setdefault(rule_pk, set()).add(object_type)
            if field_path:
                field_paths_by_rule_pk.setdefault(rule_pk, set()).add(field_path)
        return object_types_by_rule_pk, field_paths_by_rule_pk


def _build_expression(
    *,
    rule_pk: int,
    groups: list[_GroupRow],
    predicates_by_group: dict[int, list[_PredicateRow]],
) -> Expression:
    by_parent: dict[int | None, list[_GroupRow]] = {}
    for group in groups:
        by_parent.setdefault(group.parent_group_id, []).append(group)

    roots = by_parent.get(None, [])
    if len(roots) != 1:
        raise RuleStoreContractError(
            f"Rule {rule_pk} must have exactly one root group; got {len(roots)}."
        )
    root = roots[0]

    def _build_group(group: _GroupRow) -> Group:
        children: list[Expression] = []
        nested_groups = sorted(
            by_parent.get(group.group_id, []), key=lambda item: item.position
        )
        for nested in nested_groups:
            children.append(_build_group(nested))

        predicates = predicates_by_group.get(group.group_id, [])
        for predicate in sorted(predicates, key=lambda item: item.position):
            children.append(
                Condition(
                    signal=predicate.field_path,
                    op=_parse_compare_op(predicate.comparator),
                    threshold=_normalize_operand(predicate.operand_json),
                    window=Window(
                        size_seconds=predicate.window_size_seconds,
                        slide_seconds=predicate.window_slide_seconds,
                    ),
                    market_scope=predicate.market_scope,  # type: ignore[arg-type]
                )
            )
        if not children:
            raise RuleStoreContractError(
                f"Group {group.group_id} in rule {rule_pk} has no children."
            )
        return Group(
            op=BoolOp(group.bool_op),
            children=children,
        )

    return _build_group(root)


def _parse_compare_op(raw: str) -> CompareOp:
    normalized = raw.strip().lower()
    aliases = {
        "equal": CompareOp.EQ,
        "not_equal": CompareOp.NE,
        "greater": CompareOp.GT,
        "greater_or_equal": CompareOp.GTE,
        "less": CompareOp.LT,
        "less_or_equal": CompareOp.LTE,
        "in": CompareOp.IN,
        "not_in": CompareOp.NOT_IN,
        "contains": CompareOp.CONTAINS,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return CompareOp(normalized)
    except ValueError as exc:
        raise RuleStoreContractError(f"Unsupported comparator in DB: {raw}") from exc


def _normalize_operand(value: Any) -> Any:
    if value is None:
        raise RuleStoreContractError("Predicate operand_json cannot be null.")
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return value.strip()
        return value
    if isinstance(value, list):
        normalized: list[Any] = []
        for item in value:
            if not isinstance(item, (str, int, float, bool)):
                raise RuleStoreContractError(
                    "List operand contains unsupported item type."
                )
            normalized.append(item.strip() if isinstance(item, str) else item)
        return normalized
    raise RuleStoreContractError(
        f"Unsupported operand type from DB: {type(value).__name__}"
    )


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise RuleStoreContractError(
        f"Expected JSON object from DB, got {type(value).__name__}"
    )


def _to_rule_backend_error(exc: Exception, *, operation: str) -> RuleStoreBackendError:
    msg = str(exc).lower()
    if "does not exist" in msg and "relation" in msg:
        return RuleStoreBackendError(
            "Postgres schema is missing rule tables. "
            "Apply SQL migrations before enabling database rules."
        )
    return RuleStoreBackendError(f"Postgres rule store failed to {operation}: {exc}")


def _collect_expression_signals(expression: Expression) -> set[str]:
    if isinstance(expression, Condition):
        return {expression.signal}
    result: set[str] = set()
    for child in expression.children:
        result.update(_collect_expression_signals(child))
    return result
