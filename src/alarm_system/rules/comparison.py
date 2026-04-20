from __future__ import annotations

from datetime import datetime
from operator import eq, ge, gt, le, lt, ne
from typing import Any

from alarm_system.rules_dsl import CompareOp


def compare_values(op: CompareOp, observed: Any, threshold: Any) -> bool:
    binary = _BINARY_COMPARATORS.get(op)
    if binary is not None:
        return binary(observed, threshold)
    if op is CompareOp.IN:
        return _in_list_like(observed, threshold, negate=False)
    if op is CompareOp.NOT_IN:
        return _in_list_like(observed, threshold, negate=True)
    if op is CompareOp.CONTAINS:
        return _contains_value(observed, threshold)
    raise ValueError(f"Unsupported compare operation: {op.value}")


def normalize_observed_for_threshold(observed: Any, threshold: Any) -> Any:
    if observed is None:
        return None
    if isinstance(threshold, bool):
        return _normalize_bool(observed)
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        return _normalize_numeric(observed)
    if isinstance(threshold, str):
        return _normalize_string(observed)
    return observed


_BINARY_COMPARATORS = {
    CompareOp.GT: gt,
    CompareOp.GREATER: gt,
    CompareOp.GTE: ge,
    CompareOp.GREATER_OR_EQUAL: ge,
    CompareOp.LT: lt,
    CompareOp.LESS: lt,
    CompareOp.LTE: le,
    CompareOp.LESS_OR_EQUAL: le,
    CompareOp.EQ: eq,
    CompareOp.EQUAL: eq,
    CompareOp.NE: ne,
    CompareOp.NOT_EQUAL: ne,
}


def _in_list_like(observed: Any, threshold: Any, *, negate: bool) -> bool:
    if not isinstance(threshold, (list, tuple, set)):
        raise ValueError("IN/NOT_IN comparator requires list-like threshold")
    result = observed in threshold
    return not result if negate else result


def _contains_value(observed: Any, threshold: Any) -> bool:
    if isinstance(observed, str):
        return str(threshold) in observed
    if isinstance(observed, (list, tuple, set)):
        return threshold in observed
    raise ValueError("CONTAINS comparator requires string or list-like observed")


def _normalize_bool(observed: Any) -> Any:
    if isinstance(observed, bool):
        return observed
    if not isinstance(observed, str):
        return observed
    normalized = observed.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return observed


def _normalize_numeric(observed: Any) -> Any:
    if isinstance(observed, bool):
        return observed
    if isinstance(observed, (int, float)):
        return float(observed)
    if not isinstance(observed, str):
        return observed
    stripped = observed.strip()
    if not stripped:
        return observed
    try:
        return float(stripped)
    except ValueError:
        return observed


def _normalize_string(observed: Any) -> str:
    if isinstance(observed, datetime):
        return observed.isoformat()
    return str(observed)
