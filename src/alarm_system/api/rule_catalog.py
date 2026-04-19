"""Load server rule catalog from ``ALARM_RULES_PATH`` (same file as API whitelist).

Cached by file mtime so the Telegram wizard and ``GET /internal/rules`` stay
consistent without reading the file on every callback.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from alarm_system.rules_dsl import AlertRuleV1

_CACHE_MTIME: float | None = None
_CACHE_RULES: list[AlertRuleV1] | None = None


def _rules_path() -> Path | None:
    raw = os.getenv("ALARM_RULES_PATH")
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw).strip())


def load_rules_cached(*, force_reload: bool = False) -> list[AlertRuleV1]:
    """Return rules sorted by ``(rule_id, version)``. Empty if path unset/missing."""

    global _CACHE_MTIME, _CACHE_RULES

    path = _rules_path()
    if path is None or not path.is_file():
        _CACHE_MTIME = None
        _CACHE_RULES = []
        return []

    mtime = path.stat().st_mtime
    if not force_reload and _CACHE_MTIME == mtime and _CACHE_RULES is not None:
        return list(_CACHE_RULES)

    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise ValueError("ALARM_RULES_PATH must contain a JSON array of rules.")
    rules = [AlertRuleV1.model_validate(raw) for raw in content]
    sorted_rules = sorted(rules, key=lambda r: (r.rule_id, r.version))
    _CACHE_MTIME = mtime
    _CACHE_RULES = sorted_rules
    return list(sorted_rules)


def catalog_identity_hash(rules: list[AlertRuleV1]) -> str:
    """Short hash of ordered (rule_id, version) for wizard session validation."""

    raw = "|".join(f"{r.rule_id}:{r.version}" for r in rules)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def invalidate_rule_catalog_cache() -> None:
    """Test hook: clear mtime cache."""

    global _CACHE_MTIME, _CACHE_RULES
    _CACHE_MTIME = None
    _CACHE_RULES = None


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
