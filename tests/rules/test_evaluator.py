from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from alarm_system.rules.evaluator import RuleEvaluator
from alarm_system.rules_dsl import AlertRuleV1, BoolOp, CompareOp, Condition, Group, Window


def _rule_from_payload(expression: dict[str, object]) -> AlertRuleV1:
    return AlertRuleV1.model_validate(
        {
            "rule_id": "r-eval",
            "tenant_id": "tenant-1",
            "name": "Eval rule",
            "rule_type": "volume_spike_5m",
            "version": 3,
            "expression": expression,
            "filters": {"category_tags": ["Politics"]},
        }
    )


class RuleEvaluatorTests(unittest.TestCase):
    def test_not_expression_with_single_child_is_supported(self) -> None:
        rule = _rule_from_payload(
            {
                "op": "NOT",
                "children": [
                    {
                        "signal": "spread_bps",
                        "op": "lte",
                        "threshold": 120,
                        "window": {"size_seconds": 60, "slide_seconds": 10},
                    }
                ],
            }
        )
        evaluator = RuleEvaluator()
        result = evaluator.evaluate(rule=rule, signal_values={"spread_bps": 140.0})

        self.assertTrue(result.triggered)
        self.assertEqual(len(result.reason.predicates), 1)

    def test_not_expression_with_multiple_children_fails_validation(self) -> None:
        with self.assertRaises(ValidationError):
            _rule_from_payload(
                {
                    "op": "NOT",
                    "children": [
                        {
                            "signal": "spread_bps",
                            "op": "lte",
                            "threshold": 120,
                            "window": {"size_seconds": 60, "slide_seconds": 10},
                        },
                        {
                            "signal": "price_return_5m_pct",
                            "op": "gte",
                            "threshold": 2.5,
                            "window": {"size_seconds": 300, "slide_seconds": 30},
                        },
                    ],
                }
            )

    def test_not_expression_defensive_check_raises_if_validation_was_bypassed(self) -> None:
        invalid_group = Group.model_construct(
            op=BoolOp.NOT,
            children=[
                Condition.model_construct(
                    signal="spread_bps",
                    op=CompareOp.LTE,
                    threshold=120,
                    window=Window.model_construct(size_seconds=60, slide_seconds=10),
                    market_scope="single_market",
                ),
                Condition.model_construct(
                    signal="price_return_5m_pct",
                    op=CompareOp.GTE,
                    threshold=2.5,
                    window=Window.model_construct(size_seconds=300, slide_seconds=30),
                    market_scope="single_market",
                ),
            ],
        )
        rule = _rule_from_payload(
            {
                "signal": "spread_bps",
                "op": "lte",
                "threshold": 120,
                "window": {"size_seconds": 60, "slide_seconds": 10},
            }
        )
        rule = rule.model_copy(update={"expression": invalid_group})

        evaluator = RuleEvaluator()
        with self.assertRaises(ValueError):
            evaluator.evaluate(rule=rule, signal_values={"spread_bps": 100.0})

    def test_and_expression_builds_reason_json(self) -> None:
        rule = _rule_from_payload(
            {
                "op": "AND",
                "children": [
                    {
                        "signal": "price_return_5m_pct",
                        "op": "gte",
                        "threshold": 2.5,
                        "window": {"size_seconds": 300, "slide_seconds": 30},
                    },
                    {
                        "signal": "spread_bps",
                        "op": "lte",
                        "threshold": 120,
                        "window": {"size_seconds": 60, "slide_seconds": 10},
                    },
                ],
            }
        )
        evaluator = RuleEvaluator()
        result = evaluator.evaluate(
            rule=rule,
            signal_values={"price_return_5m_pct": 2.8, "spread_bps": 100.0},
            matched_filters={"category_tags": "politics"},
            evaluated_at=datetime(2026, 4, 16, 13, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result.triggered)
        self.assertEqual(result.reason.rule_id, "r-eval")
        self.assertEqual(result.reason.rule_version, 3)
        self.assertEqual(len(result.reason.predicates), 2)
        self.assertEqual(result.reason.matched_filters["category_tags"], "politics")
        self.assertIn("matched", result.reason.summary)

    def test_missing_signal_fails_condition_with_explainability_note(self) -> None:
        rule = _rule_from_payload(
            {
                "signal": "book_imbalance_topN",
                "op": "gte",
                "threshold": 0.2,
                "window": {"size_seconds": 120, "slide_seconds": 10},
            }
        )
        evaluator = RuleEvaluator()
        result = evaluator.evaluate(rule=rule, signal_values={})

        self.assertFalse(result.triggered)
        self.assertEqual(result.reason.predicates[0].note, "missing_signal")
