from __future__ import annotations

import unittest


class LogicalSplitTests(unittest.TestCase):
    def test_app_entrypoints_import(self) -> None:
        from alarm_system.run_api import main as api_main
        from alarm_system.service_runtime import main as worker_main

        self.assertTrue(callable(api_main))
        self.assertTrue(callable(worker_main))

    def test_core_reexport_surface(self) -> None:
        from alarm_system import (
            Alert,
            RuleRuntime,
            RuntimeObservability,
        )
        from alarm_system import __all__ as core_exports

        self.assertIsNotNone(Alert)
        self.assertIsNotNone(RuleRuntime)
        self.assertIsNotNone(RuntimeObservability)
        self.assertIn("Alert", core_exports)
        self.assertIn("RuleRuntime", core_exports)
        self.assertIn("RuntimeObservability", core_exports)
