from __future__ import annotations

from alarm_system.rules_dsl import CompareOp


def compare_values(op: CompareOp, observed: float, threshold: float) -> bool:
    if op is CompareOp.GT:
        return observed > threshold
    if op is CompareOp.GTE:
        return observed >= threshold
    if op is CompareOp.LT:
        return observed < threshold
    if op is CompareOp.LTE:
        return observed <= threshold
    if op is CompareOp.EQ:
        return observed == threshold
    if op is CompareOp.NE:
        return observed != threshold
    raise ValueError(f"Unsupported compare operation: {op.value}")
