from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alarm_system.api.migrations import (
    apply_sql_migrations,
    should_auto_apply_sql_migrations,
)


class _FakeCursor:
    def __init__(self, *, fail: bool = False) -> None:
        self.executed_sql: list[str] = []
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str) -> None:
        if self.fail:
            raise RuntimeError("db failure")
        self.executed_sql.append(sql)


class _FakeConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.cursor_obj = _FakeCursor(fail=fail)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


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

    def test_apply_sql_migrations_noop_when_no_scripts(self) -> None:
        fake_connection = _FakeConnection()

        class _FakePsycopg:
            @staticmethod
            def connect(dsn: str):
                return fake_connection

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "alarm_system.api.migrations._resolve_migrations_dir",
                return_value=Path(tmp_dir),
            ), patch.dict("sys.modules", {"psycopg": _FakePsycopg}):
                apply_sql_migrations(postgres_dsn="postgresql://localhost/test")
        self.assertEqual(fake_connection.cursor_obj.executed_sql, [])
        self.assertFalse(fake_connection.committed)

    def test_apply_sql_migrations_runs_scripts_in_lexical_order(self) -> None:
        fake_connection = _FakeConnection()

        class _FakePsycopg:
            @staticmethod
            def connect(dsn: str):
                return fake_connection

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "002_second.sql").write_text(
                "CREATE TABLE second();",
                encoding="utf-8",
            )
            (root / "001_first.sql").write_text(
                "CREATE TABLE first();",
                encoding="utf-8",
            )
            with patch(
                "alarm_system.api.migrations._resolve_migrations_dir",
                return_value=root,
            ), patch.dict("sys.modules", {"psycopg": _FakePsycopg}):
                apply_sql_migrations(postgres_dsn="postgresql://localhost/test")

        self.assertEqual(
            fake_connection.cursor_obj.executed_sql,
            ["CREATE TABLE first();", "CREATE TABLE second();"],
        )
        self.assertTrue(fake_connection.committed)

    def test_apply_sql_migrations_raises_when_psycopg_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")

            original_import = __import__

            def _import_hook(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "psycopg":
                    raise ImportError("missing")
                return original_import(name, globals, locals, fromlist, level)

            with patch(
                "alarm_system.api.migrations._resolve_migrations_dir",
                return_value=root,
            ), patch("builtins.__import__", side_effect=_import_hook):
                with self.assertRaises(RuntimeError) as ctx:
                    apply_sql_migrations(
                        postgres_dsn="postgresql://localhost/test"
                    )
        self.assertIn("psycopg", str(ctx.exception))

    def test_apply_sql_migrations_wraps_runtime_sql_error(self) -> None:
        failing_connection = _FakeConnection(fail=True)

        class _FakePsycopg:
            @staticmethod
            def connect(dsn: str):
                return failing_connection

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
            with patch(
                "alarm_system.api.migrations._resolve_migrations_dir",
                return_value=root,
            ), patch.dict("sys.modules", {"psycopg": _FakePsycopg}):
                with self.assertRaises(RuntimeError) as ctx:
                    apply_sql_migrations(
                        postgres_dsn="postgresql://localhost/test"
                    )
        self.assertIn("Failed to apply SQL migrations", str(ctx.exception))

    def test_new_market_cleanup_migration_is_targeted(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        migration = (
            repo_root
            / "src"
            / "alarm_system"
            / "migrations"
            / "0004_new_market_filters_cleanup.sql"
        )
        sql = migration.read_text(encoding="utf-8")
        self.assertIn("payload_json->>'alert_type' = 'new_market_liquidity'", sql)
        self.assertIn("?| ARRAY[", sql)
        self.assertIn("'return_1m_pct_min'", sql)
        self.assertIn("'liquidity_usd_min'", sql)
