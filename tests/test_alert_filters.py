"""Unit tests for alert-level ``filters_json`` parsing and evaluation."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from alarm_system.alert_filters import (
    effective_min_smart_score,
    effective_require_event_tag,
    filters_from_command_options,
    matched_filter_evidence,
    merge_filter_overrides,
    parse_filter_kv_line,
    passes_alert_filters,
    validated_filters_dict,
)
from alarm_system.rules_dsl import AlertRuleV1, RuleFilters, RuleType


class PassesAlertFiltersTests(unittest.TestCase):
    def test_empty_passes(self) -> None:
        self.assertTrue(
            passes_alert_filters({}, signal_values={}, event_tags=set())
        )

    def test_liquidity_min_blocks_when_below(self) -> None:
        self.assertFalse(
            passes_alert_filters(
                {"liquidity_usd_min": 1000.0},
                signal_values={"liquidity_usd": 500.0},
                event_tags=set(),
            )
        )

    def test_liquidity_min_passes(self) -> None:
        self.assertTrue(
            passes_alert_filters(
                {"liquidity_usd_min": 1000.0},
                signal_values={"liquidity_usd": 1500.0},
                event_tags=set(),
            )
        )

    def test_missing_signal_fails(self) -> None:
        self.assertFalse(
            passes_alert_filters(
                {"liquidity_usd_min": 1.0},
                signal_values={},
                event_tags=set(),
            )
        )

    def test_category_tags_intersection(self) -> None:
        self.assertFalse(
            passes_alert_filters(
                {"category_tags": ["politics"]},
                signal_values={"liquidity_usd": 1.0},
                event_tags={"crypto"},
            )
        )
        self.assertTrue(
            passes_alert_filters(
                {"category_tags": ["politics"]},
                signal_values={"liquidity_usd": 1.0},
                event_tags={"politics"},
            )
        )


class EffectiveTraderMergeTests(unittest.TestCase):
    def test_min_smart_user_tightens(self) -> None:
        rf = RuleFilters(min_smart_score=80.0)
        self.assertEqual(
            effective_min_smart_score(rf, {"min_smart_score": 90.0}),
            90.0,
        )

    def test_min_smart_user_cannot_loosen_below_rule(self) -> None:
        rf = RuleFilters(min_smart_score=80.0)
        self.assertEqual(
            effective_min_smart_score(rf, {"min_smart_score": 50.0}),
            80.0,
        )


class EffectiveRequireEventTagTests(unittest.TestCase):
    def test_rule_only(self) -> None:
        rf = RuleFilters(require_event_tag="breaking")
        self.assertEqual(effective_require_event_tag(rf, {}), "breaking")

    def test_alert_only(self) -> None:
        rf = RuleFilters()
        self.assertEqual(
            effective_require_event_tag(rf, {"require_event_tag": "crypto"}),
            "crypto",
        )

    def test_alert_overrides_rule(self) -> None:
        rf = RuleFilters(require_event_tag="breaking")
        self.assertEqual(
            effective_require_event_tag(
                rf, {"require_event_tag": "Politics"}
            ),
            "politics",
        )


class MatchedFilterEvidenceTests(unittest.TestCase):
    def test_includes_require_event_tag_and_threshold_strings(self) -> None:
        rule = AlertRuleV1.model_validate(
            {
                "rule_id": "r-ev",
                "tenant_id": "t",
                "name": "ev",
                "rule_type": "trader_position_update",
                "version": 1,
                "expression": {
                    "signal": "PositionOpened",
                    "op": "eq",
                    "threshold": 1,
                    "window": {"size_seconds": 60, "slide_seconds": 10},
                },
                "filters": {
                    "category_tags": ["Politics"],
                    "require_event_tag": "breaking",
                    "min_smart_score": 70.0,
                    "min_account_age_days": 100,
                },
            }
        )
        ev = matched_filter_evidence(
            rule,
            {},
            rule_tags={"politics"},
            event_tags={"politics", "breaking"},
            signal_values={"smart_score": 92.0, "account_age_days": 400.0},
        )
        self.assertEqual(ev["category_tags"], "politics")
        self.assertEqual(ev["require_event_tag"], "breaking")
        self.assertIn("threshold=70", ev["min_smart_score"])
        self.assertIn("observed=92", ev["min_smart_score"])
        self.assertIn("threshold=100", ev["min_account_age_days"])
        self.assertIn("observed=400", ev["min_account_age_days"])


class ValidatedFiltersDictTests(unittest.TestCase):
    def test_rejects_unknown_key(self) -> None:
        with self.assertRaises(ValidationError):
            validated_filters_dict(
                RuleType.VOLUME_SPIKE_5M,
                {"not_a_real_key": 1.0},
            )

    def test_accepts_numeric_bundle(self) -> None:
        out = validated_filters_dict(
            RuleType.VOLUME_SPIKE_5M,
            {
                "return_1m_pct_min": 1.2,
                "liquidity_usd_min": 50000.0,
            },
        )
        self.assertEqual(out["return_1m_pct_min"], 1.2)
        self.assertEqual(out["liquidity_usd_min"], 50000.0)

    def test_normalizes_require_event_tag(self) -> None:
        out = validated_filters_dict(
            RuleType.VOLUME_SPIKE_5M,
            {"require_event_tag": "BREAKING"},
        )
        self.assertEqual(out["require_event_tag"], "breaking")


class ParseAndCommandOptionsTests(unittest.TestCase):
    def test_parse_filter_kv_line(self) -> None:
        raw = parse_filter_kv_line(
            "return_1m_pct_min=1.2 liquidity_usd_min=100000"
        )
        self.assertEqual(raw["return_1m_pct_min"], "1.2")
        self.assertEqual(raw["liquidity_usd_min"], "100000")

    def test_filters_from_command_options(self) -> None:
        opts = {
            "alert_id": "x",
            "cooldown": "60",
            "liquidity_usd_min": "200000",
        }
        out = filters_from_command_options(
            opts,
            alert_type=RuleType.VOLUME_SPIKE_5M,
        )
        self.assertEqual(out["liquidity_usd_min"], 200000.0)

    def test_merge_filter_overrides(self) -> None:
        base = {"liquidity_usd_min": 100.0}
        merged = merge_filter_overrides(
            base,
            {"liquidity_usd_min": 200.0},
        )
        self.assertEqual(merged["liquidity_usd_min"], 200.0)
