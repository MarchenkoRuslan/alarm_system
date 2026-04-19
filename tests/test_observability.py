from __future__ import annotations

import unittest

from alarm_system.observability import RuntimeObservability


class ObservabilityTests(unittest.TestCase):
    def test_event_to_enqueue_p95_slo_passes_under_threshold(self) -> None:
        obs = RuntimeObservability()
        for value in [120.0, 200.0, 300.0, 450.0, 700.0]:
            obs.observe_timing_ms("event_to_enqueue_ms", value)

        result = obs.check_event_to_enqueue_slo(threshold_ms=1000.0)

        self.assertTrue(result.passed)
        self.assertLessEqual(result.p95_ms, 1000.0)

    def test_event_to_enqueue_p95_slo_fails_when_latency_is_high(self) -> None:
        obs = RuntimeObservability()
        for value in [100.0, 150.0, 200.0, 1500.0, 1800.0]:
            obs.observe_timing_ms("event_to_enqueue_ms", value)

        result = obs.check_event_to_enqueue_slo(threshold_ms=1000.0)

        self.assertFalse(result.passed)
        self.assertGreater(result.p95_ms, 1000.0)

    def test_observe_ratio_appears_in_p95_ratios_snapshot(self) -> None:
        obs = RuntimeObservability()
        obs.observe_ratio("prefilter_hit_ratio", 0.25)
        obs.observe_ratio("prefilter_hit_ratio", 0.75)
        snapshot = obs.snapshot()

        self.assertGreaterEqual(obs.p95_ratio("prefilter_hit_ratio"), 0.0)
        self.assertIn("prefilter_hit_ratio", snapshot["p95_ratios"])
