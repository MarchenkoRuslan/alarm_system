from __future__ import annotations

import unittest

from alarm_system.load_harness import (
    LoadHarnessResult,
    LockedLoadProfile,
    run_locked_profile_smoke,
)


class Phase4LoadHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_locked_profile_smoke_meets_slo(self) -> None:
        profile = LockedLoadProfile(
            baseline_eps=200,
            burst_multiplier=3,
            baseline_window_sec=1,
            burst_window_sec=1,
            active_alerts=5000,
            target_p95_ms=1000.0,
        )
        result = await run_locked_profile_smoke(profile)

        self.assertIsInstance(result, LoadHarnessResult)
        self.assertEqual(result.baseline_events, 200)
        self.assertEqual(result.burst_events, 600)
        self.assertEqual(result.total_events, 800)
        self.assertEqual(result.active_alerts, 5000)
        self.assertTrue(result.slo.passed)
        self.assertLessEqual(result.slo.p95_ms, 1000.0)
