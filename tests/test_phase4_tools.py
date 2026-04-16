from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from contextlib import redirect_stdout
from unittest.mock import patch

from alarm_system.load_harness import (
    LoadHarnessResult,
    LoadHarnessTimeoutError,
)
from alarm_system.observability import SLOCheckResult
from alarm_system.phase4_tools import (
    run_phase4_load_main,
    run_phase4_rollback_main,
)
from alarm_system.rollback_drill import RollbackDrillResult


class Phase4ToolsTests(unittest.TestCase):
    def test_run_phase4_load_main_uses_long_profile(self) -> None:
        captured_profile = None

        async def _fake_run(profile):  # noqa: ANN001
            nonlocal captured_profile
            captured_profile = profile
            return LoadHarnessResult(
                total_events=0,
                baseline_events=0,
                burst_events=0,
                active_alerts=profile.active_alerts,
                decisions_emitted=0,
                dispatched_queued=0,
                slo=SLOCheckResult(
                    metric="event_to_enqueue_ms",
                    p95_ms=10.0,
                    threshold_ms=1000.0,
                    passed=True,
                ),
            )

        with patch(
            "alarm_system.phase4_tools.run_locked_profile_smoke",
            new=_fake_run,
        ), patch(
            "sys.argv",
            [
                "run-phase4-load",
                "--profile",
                "long",
                "--dispatch-only",
                "--max-runtime-sec",
                "120",
            ],
        ):
            run_phase4_load_main()

        self.assertIsNotNone(captured_profile)
        self.assertEqual(captured_profile.baseline_window_sec, 60)
        self.assertEqual(captured_profile.burst_window_sec, 60)
        self.assertEqual(captured_profile.tag_buckets, 5000)
        self.assertFalse(captured_profile.run_end_to_end)
        self.assertEqual(captured_profile.max_runtime_sec, 120.0)
        self.assertEqual(captured_profile.progress_every_events, 2000)

    def test_run_phase4_rollback_main_returns_zero_when_passed(self) -> None:
        async def _fake_rollback():  # noqa: ANN001
            return RollbackDrillResult(
                freeze_non_critical_applied=True,
                load_gate_passed=True,
                replay_parity_passed=True,
                idempotent_replay_passed=True,
            )

        buffer = io.StringIO()
        with patch(
            "alarm_system.phase4_tools.run_rollback_drill_smoke",
            new=_fake_rollback,
        ), patch("sys.argv", ["run-phase4-rollback"]), redirect_stdout(
            buffer
        ):
            run_phase4_rollback_main()
        payload = json.loads(buffer.getvalue().strip())
        self.assertTrue(payload["passed"])

    def test_run_phase4_load_main_returns_exit_2_on_timeout(self) -> None:
        async def _fake_timeout(profile):  # noqa: ANN001
            raise LoadHarnessTimeoutError("timeout")

        stderr = io.StringIO()
        with patch(
            "alarm_system.phase4_tools.run_locked_profile_smoke",
            new=_fake_timeout,
        ), patch(
            "sys.argv",
            ["run-phase4-load", "--profile", "long"],
        ), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as ctx:
                run_phase4_load_main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('"error": "timeout"', stderr.getvalue())
