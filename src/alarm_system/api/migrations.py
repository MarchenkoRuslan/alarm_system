from __future__ import annotations

import os
from pathlib import Path


def apply_sql_migrations(*, postgres_dsn: str) -> None:
    """Apply SQL files from migrations folder in lexical order."""
    migration_dir = _resolve_migrations_dir()
    scripts = sorted(migration_dir.glob("*.sql"))
    if not scripts:
        return

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'psycopg' package is required to apply SQL migrations."
        ) from exc

    try:
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            for script in scripts:
                sql = script.read_text(encoding="utf-8")
                if not sql.strip():
                    continue
                cur.execute(sql)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to apply SQL migrations: {exc}") from exc


def should_auto_apply_sql_migrations() -> bool:
    value = os.getenv("ALARM_AUTO_APPLY_SQL_MIGRATIONS")
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        "Invalid ALARM_AUTO_APPLY_SQL_MIGRATIONS value. "
        "Use one of true/false/1/0/yes/no/on/off."
    )


def _resolve_migrations_dir() -> Path:
    # __file__ is .../alarm_system/api/migrations.py whether running from the
    # source tree or from a wheel installed in site-packages.
    # The SQL files live in .../alarm_system/migrations/ (two levels up from api/).
    return Path(__file__).resolve().parent.parent / "migrations"
