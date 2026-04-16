from __future__ import annotations

import unittest
from unittest.mock import patch

from alarm_system.api.migrations import should_auto_apply_sql_migrations


class ApiMigrationsTests(unittest.TestCase):
    def test_should_auto_apply_sql_migrations_defaults_true(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(should_auto_apply_sql_migrations())

    def test_should_auto_apply_sql_migrations_rejects_invalid_value(self) -> None:
        with patch.dict(
            "os.environ",
            {"ALARM_AUTO_APPLY_SQL_MIGRATIONS": "maybe"},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                should_auto_apply_sql_migrations()
