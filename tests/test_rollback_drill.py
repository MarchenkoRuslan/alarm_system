from __future__ import annotations

import unittest

from alarm_system.rollback_drill import (
    RollbackDrillResult,
    run_rollback_drill_smoke,
)


class RollbackDrillTests(unittest.IsolatedAsyncioTestCase):
    async def test_rollback_drill_smoke_passes(self) -> None:
        result = await run_rollback_drill_smoke()

        self.assertIsInstance(result, RollbackDrillResult)
        self.assertTrue(result.freeze_non_critical_applied)
        self.assertTrue(result.load_gate_passed)
        self.assertTrue(result.replay_parity_passed)
        self.assertTrue(result.idempotent_replay_passed)
        self.assertTrue(result.passed)
