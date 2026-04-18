from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from alarm_system.rules.comparison import compare_values
from alarm_system.rules_dsl import (
    AlertRuleV1,
    BoolOp,
    Condition,
    Group,
    PredicateExplanation,
    TriggerReason,
)


@dataclass(frozen=True)
class EvaluationResult:
    triggered: bool
    reason: TriggerReason


class RuleEvaluator:
    def evaluate(
        self,
        rule: AlertRuleV1,
        signal_values: Mapping[str, float],
        matched_filters: dict[str, str] | None = None,
        evaluated_at: datetime | None = None,
    ) -> EvaluationResult:
        predicate_results: list[PredicateExplanation] = []
        triggered = self._eval_expression(
            expression=rule.expression,
            signal_values=signal_values,
            out_predicates=predicate_results,
        )
        summary = self._build_summary(rule=rule, triggered=triggered, predicates=predicate_results)
        reason = TriggerReason(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            evaluated_at=evaluated_at or datetime.now(timezone.utc),
            predicates=predicate_results,
            matched_filters=matched_filters or {},
            summary=summary,
        )
        return EvaluationResult(triggered=triggered, reason=reason)

    def _eval_expression(
        self,
        expression: Condition | Group,
        signal_values: Mapping[str, float],
        out_predicates: list[PredicateExplanation],
    ) -> bool:
        match expression:
            case Condition():
                observed = signal_values.get(expression.signal)
                if observed is None:
                    passed = False
                    observed_value = 0.0
                    note = "missing_signal"
                else:
                    observed_value = float(observed)
                    passed = compare_values(
                        expression.op, observed_value, expression.threshold
                    )
                    note = None
                out_predicates.append(
                    PredicateExplanation(
                        signal=expression.signal,
                        op=expression.op,
                        observed_value=observed_value,
                        threshold=expression.threshold,
                        passed=passed,
                        window_seconds=expression.window.size_seconds,
                        note=note,
                    )
                )
                return passed
            case Group():
                if not expression.children:
                    return False
                match expression.op:
                    case BoolOp.NOT:
                        if len(expression.children) != 1:
                            raise ValueError(
                                "NOT expression must have exactly one child"
                            )
                        return not self._eval_expression(
                            expression.children[0],
                            signal_values=signal_values,
                            out_predicates=out_predicates,
                        )
                    case BoolOp.AND:
                        child_results = [
                            self._eval_expression(
                                child,
                                signal_values=signal_values,
                                out_predicates=out_predicates,
                            )
                            for child in expression.children
                        ]
                        return all(child_results)
                    case BoolOp.OR:
                        child_results = [
                            self._eval_expression(
                                child,
                                signal_values=signal_values,
                                out_predicates=out_predicates,
                            )
                            for child in expression.children
                        ]
                        return any(child_results)

    @staticmethod
    def _build_summary(
        rule: AlertRuleV1,
        triggered: bool,
        predicates: list[PredicateExplanation],
    ) -> str:
        passed = sum(1 for predicate in predicates if predicate.passed)
        total = len(predicates)
        state = "matched" if triggered else "did_not_match"
        return f"{rule.rule_id}@v{rule.version}:{state}:{passed}/{total}"
