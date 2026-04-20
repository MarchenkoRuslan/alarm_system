"""Load server rule catalog.

Strict SSOT mode (`ALARM_USE_DATABASE_RULES=true`) reads only Postgres and
does not fall back to file-based catalogs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from alarm_system.rule_store import PostgresRuleStore, RuleStoreBackendError
from alarm_system.rules_dsl import AlertRuleV1

_CACHE_MTIME: float | None = None
_CACHE_DB_VERSION: int | None = None
_CACHE_RULES: list[AlertRuleV1] | None = None
_CACHE_SOURCE: Literal["db", "file"] | None = None


def _rules_path() -> Path | None:
    raw = os.getenv("ALARM_RULES_PATH")
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw).strip())


def _use_db_rules() -> bool:
    return os.getenv("ALARM_USE_DATABASE_RULES", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_rule_catalog_configured() -> bool:
    if _use_db_rules():
        return True
    return _rules_path() is not None


def load_rules_cached(*, force_reload: bool = False) -> list[AlertRuleV1]:
    """Return rules sorted by ``(rule_id, version)``. Empty if path unset/missing."""
    if _use_db_rules():
        return _load_rules_from_db(force_reload=force_reload)
    return _load_rules_from_file(force_reload=force_reload)


def _load_rules_from_db(*, force_reload: bool) -> list[AlertRuleV1]:
    if _CACHE_SOURCE == "file":
        invalidate_rule_catalog_cache()
    dsn = os.getenv("ALARM_POSTGRES_DSN")
    if dsn is None or not dsn.strip():
        raise ValueError(
            "ALARM_POSTGRES_DSN is required when ALARM_USE_DATABASE_RULES=true."
        )
    store = PostgresRuleStore(dsn.strip())
    version = store.get_active_version()
    cached = _db_cache_hit(version=version, force_reload=force_reload)
    if cached is not None:
        return cached
    try:
        snapshot = store.get_active_snapshot()
    except RuleStoreBackendError as exc:
        raise ValueError(f"Failed to load rules from Postgres: {exc}") from exc
    if not snapshot.rules:
        raise ValueError(
            "No active rules found in Postgres rule store. "
            "Strict SSOT mode does not fall back to ALARM_RULES_PATH."
        )
    sorted_rules = sorted(snapshot.rules, key=lambda r: (r.rule_id, r.version))
    _set_cache(source="db", rules=sorted_rules, db_version=snapshot.version)
    return list(sorted_rules)


def _db_cache_hit(
    *,
    version: int | None,
    force_reload: bool,
) -> list[AlertRuleV1] | None:
    if force_reload:
        return None
    if _CACHE_SOURCE != "db" or _CACHE_RULES is None or _CACHE_DB_VERSION is None:
        return None
    if _CACHE_DB_VERSION != version:
        return None
    return list(_CACHE_RULES)


def _load_rules_from_file(*, force_reload: bool) -> list[AlertRuleV1]:
    path = _rules_path()
    if _CACHE_SOURCE == "db":
        invalidate_rule_catalog_cache()
    if path is None or not path.is_file():
        _set_cache(source="file", rules=[], mtime=None)
        return []
    mtime = path.stat().st_mtime
    cached = _file_cache_hit(mtime=mtime, force_reload=force_reload)
    if cached is not None:
        return cached
    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise ValueError("ALARM_RULES_PATH must contain a JSON array of rules.")
    rules = [AlertRuleV1.model_validate(raw) for raw in content]
    sorted_rules = sorted(rules, key=lambda r: (r.rule_id, r.version))
    _set_cache(source="file", rules=sorted_rules, mtime=mtime)
    return list(sorted_rules)


def _file_cache_hit(
    *,
    mtime: float,
    force_reload: bool,
) -> list[AlertRuleV1] | None:
    if force_reload:
        return None
    if _CACHE_SOURCE != "file" or _CACHE_RULES is None:
        return None
    if _CACHE_MTIME != mtime:
        return None
    return list(_CACHE_RULES)


def catalog_identity_hash(rules: list[AlertRuleV1]) -> str:
    """Short hash of ordered (rule_id, version) for wizard session validation."""

    raw = "|".join(f"{r.rule_id}:{r.version}" for r in rules)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_rule_identities_cached(
    *, force_reload: bool = False
) -> set[tuple[str, int]] | None:
    if not is_rule_catalog_configured():
        return None
    rules = load_rules_cached(force_reload=force_reload)
    return {(rule.rule_id, rule.version) for rule in rules}


def invalidate_rule_catalog_cache() -> None:
    """Test hook: clear mtime cache."""

    global _CACHE_MTIME, _CACHE_DB_VERSION, _CACHE_RULES, _CACHE_SOURCE
    _CACHE_MTIME = None
    _CACHE_DB_VERSION = None
    _CACHE_RULES = None
    _CACHE_SOURCE = None


def _set_cache(
    *,
    source: Literal["db", "file"],
    rules: list[AlertRuleV1],
    db_version: int | None = None,
    mtime: float | None = None,
) -> None:
    global _CACHE_MTIME, _CACHE_DB_VERSION, _CACHE_RULES, _CACHE_SOURCE
    _CACHE_SOURCE = source
    _CACHE_RULES = list(rules)
    _CACHE_DB_VERSION = db_version if source == "db" else None
    _CACHE_MTIME = mtime if source == "file" else None


def rule_at_index(rules: list[AlertRuleV1], index: int) -> AlertRuleV1 | None:
    if index < 0 or index >= len(rules):
        return None
    return rules[index]


def parse_rule_index(arg: str) -> int | None:
    try:
        v = int(arg.strip())
    except ValueError:
        return None
    return v if v >= 0 else None
