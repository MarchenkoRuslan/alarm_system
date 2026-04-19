"""Sensitivity bundles and defaults for the alert-creation wizard.

Named profiles load from ``deploy/config/alert_presets.json`` (override via
``ALARM_ALERT_PRESETS_PATH``). Rule identities always come from
``ALARM_RULES_PATH`` via :mod:`alarm_system.api.rule_catalog` — no duplicate
scenario tuples in code.

The wizard composes ``AlertCreateRequest`` from a chosen rule + optional
preset or custom ``filters_json``. ``ALERT_CREATE_EXAMPLES`` follows the live
rule catalog when the file is available.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alarm_system.api.rule_catalog import load_rules_cached
from alarm_system.rules_dsl import AlertRuleV1, RuleType

_PRESETS_FILE_ENV = "ALARM_ALERT_PRESETS_PATH"


@dataclass(frozen=True)
class SensitivityPreset:
    """A named bundle of cooldown + filters defaults."""

    preset_id: str
    label: str
    cooldown_seconds: int
    filters_json: dict[str, Any]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _default_presets_path() -> Path:
    return _repo_root() / "deploy" / "config" / "alert_presets.json"


def _embedded_json_defaults() -> dict[str, Any]:
    """Fallback when JSON file is absent (tests, sdist without deploy/)."""

    return {
        "defaults": {"custom_path_cooldown_seconds": 180},
        "sensitivity_presets": {
            "trader_position_update": [
                {
                    "preset_id": "conservative",
                    "label": "Тихо (Conservative)",
                    "cooldown_seconds": 300,
                    "filters_json": {
                        "return_1m_pct_min": 2.0,
                        "return_5m_pct_min": 4.0,
                        "spread_bps_max": 80,
                        "imbalance_abs_min": 0.30,
                        "liquidity_usd_min": 250000,
                    },
                },
                {
                    "preset_id": "balanced",
                    "label": "Обычно (Balanced)",
                    "cooldown_seconds": 180,
                    "filters_json": {
                        "return_1m_pct_min": 1.2,
                        "return_5m_pct_min": 2.5,
                        "spread_bps_max": 120,
                        "imbalance_abs_min": 0.20,
                        "liquidity_usd_min": 100000,
                    },
                },
                {
                    "preset_id": "aggressive",
                    "label": "Агрессивно (Aggressive)",
                    "cooldown_seconds": 90,
                    "filters_json": {
                        "return_1m_pct_min": 0.7,
                        "return_5m_pct_min": 1.5,
                        "spread_bps_max": 180,
                        "imbalance_abs_min": 0.12,
                        "liquidity_usd_min": 50000,
                    },
                },
            ],
            "volume_spike_5m": [
                {
                    "preset_id": "conservative",
                    "label": "Тихо (Conservative)",
                    "cooldown_seconds": 300,
                    "filters_json": {
                        "return_1m_pct_min": 2.0,
                        "return_5m_pct_min": 4.0,
                        "spread_bps_max": 80,
                        "imbalance_abs_min": 0.30,
                        "liquidity_usd_min": 250000,
                    },
                },
                {
                    "preset_id": "balanced",
                    "label": "Обычно (Balanced)",
                    "cooldown_seconds": 180,
                    "filters_json": {
                        "return_1m_pct_min": 1.2,
                        "return_5m_pct_min": 2.5,
                        "spread_bps_max": 120,
                        "imbalance_abs_min": 0.20,
                        "liquidity_usd_min": 100000,
                    },
                },
                {
                    "preset_id": "aggressive",
                    "label": "Агрессивно (Aggressive)",
                    "cooldown_seconds": 90,
                    "filters_json": {
                        "return_1m_pct_min": 0.7,
                        "return_5m_pct_min": 1.5,
                        "spread_bps_max": 180,
                        "imbalance_abs_min": 0.12,
                        "liquidity_usd_min": 50000,
                    },
                },
            ],
            "new_market_liquidity": [
                {
                    "preset_id": "conservative",
                    "label": "Тихо (Conservative)",
                    "cooldown_seconds": 300,
                    "filters_json": {
                        "target_liquidity_usd": 250000,
                        "deferred_watch_ttl_hours": 336,
                    },
                },
                {
                    "preset_id": "balanced",
                    "label": "Обычно (Balanced)",
                    "cooldown_seconds": 180,
                    "filters_json": {
                        "target_liquidity_usd": 100000,
                        "deferred_watch_ttl_hours": 336,
                    },
                },
                {
                    "preset_id": "aggressive",
                    "label": "Агрессивно (Aggressive)",
                    "cooldown_seconds": 90,
                    "filters_json": {
                        "target_liquidity_usd": 50000,
                        "deferred_watch_ttl_hours": 336,
                    },
                },
            ],
        },
    }


def _load_presets_blob() -> dict[str, Any]:
    raw_path = os.getenv(_PRESETS_FILE_ENV)
    path = Path(raw_path) if raw_path else _default_presets_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return _embedded_json_defaults()


def _parse_one_sensitivity_list(raw_list: object) -> tuple[SensitivityPreset, ...]:
    if not isinstance(raw_list, list) or not raw_list:
        return ()
    out: list[SensitivityPreset] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        pid = item.get("preset_id")
        label = item.get("label")
        cd = item.get("cooldown_seconds")
        fj = item.get("filters_json")
        if (
            not isinstance(pid, str)
            or not isinstance(label, str)
            or not isinstance(cd, int)
            or not isinstance(fj, dict)
        ):
            continue
        out.append(
            SensitivityPreset(
                preset_id=pid,
                label=label,
                cooldown_seconds=cd,
                filters_json=dict(fj),
            )
        )
    return tuple(out)


def _parse_sensitivity_presets(
    blob: dict[str, Any],
) -> dict[RuleType, tuple[SensitivityPreset, ...]]:
    embedded = _embedded_json_defaults()["sensitivity_presets"]
    raw = blob.get("sensitivity_presets")

    out: dict[RuleType, tuple[SensitivityPreset, ...]] = {}
    if isinstance(raw, dict):
        for rule_type in RuleType:
            parsed = _parse_one_sensitivity_list(raw.get(rule_type.value))
            if parsed:
                out[rule_type] = parsed
    elif isinstance(raw, list):
        # Backward compatibility for old flat format: keep prior behavior
        # for trader/volume and use embedded safe defaults for new_market.
        parsed_flat = _parse_one_sensitivity_list(raw)
        if parsed_flat:
            out[RuleType.TRADER_POSITION_UPDATE] = parsed_flat
            out[RuleType.VOLUME_SPIKE_5M] = parsed_flat

    for rule_type in RuleType:
        if rule_type in out:
            continue
        fallback = _parse_one_sensitivity_list(embedded.get(rule_type.value))
        if fallback:
            out[rule_type] = fallback

    return out


def _parse_custom_default_cooldown(blob: dict[str, Any]) -> int:
    defaults = blob.get("defaults")
    if isinstance(defaults, dict):
        raw = defaults.get("custom_path_cooldown_seconds")
        if isinstance(raw, int) and raw >= 0:
            return raw
    return int(_embedded_json_defaults()["defaults"]["custom_path_cooldown_seconds"])


_PRESETS_BLOB = _load_presets_blob()
SENSITIVITY_PRESETS_BY_TYPE: dict[RuleType, tuple[SensitivityPreset, ...]] = (
    _parse_sensitivity_presets(
        _PRESETS_BLOB
    )
)
SENSITIVITY_PRESETS: tuple[SensitivityPreset, ...] = (
    SENSITIVITY_PRESETS_BY_TYPE[RuleType.VOLUME_SPIKE_5M]
)
DEFAULT_SENSITIVITY_PRESETS: tuple[SensitivityPreset, ...] = SENSITIVITY_PRESETS
SENSITIVITY_BY_ID_BY_TYPE: dict[RuleType, dict[str, SensitivityPreset]] = {
    rule_type: {p.preset_id: p for p in presets}
    for rule_type, presets in SENSITIVITY_PRESETS_BY_TYPE.items()
}
DEFAULT_SENSITIVITY_BY_ID: dict[str, SensitivityPreset] = (
    SENSITIVITY_BY_ID_BY_TYPE[RuleType.VOLUME_SPIKE_5M]
)
SENSITIVITY_BY_ID: dict[str, SensitivityPreset] = DEFAULT_SENSITIVITY_BY_ID
DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS: int = _parse_custom_default_cooldown(
    _PRESETS_BLOB
)


def sensitivity_presets_for(alert_type: RuleType) -> tuple[SensitivityPreset, ...]:
    return SENSITIVITY_PRESETS_BY_TYPE.get(alert_type, DEFAULT_SENSITIVITY_PRESETS)


def sensitivity_by_id_for(alert_type: RuleType) -> dict[str, SensitivityPreset]:
    return SENSITIVITY_BY_ID_BY_TYPE.get(alert_type, DEFAULT_SENSITIVITY_BY_ID)


def sensitivity_preset_for(
    alert_type: RuleType,
    preset_id: str,
) -> SensitivityPreset:
    return sensitivity_by_id_for(alert_type)[preset_id]


def default_sensitivity_for(alert_type: RuleType) -> SensitivityPreset:
    by_id = sensitivity_by_id_for(alert_type)
    return by_id.get("balanced") or next(iter(by_id.values()))


def _build_example_value(
    rule: AlertRuleV1,
    sensitivity: SensitivityPreset,
) -> dict[str, Any]:
    return {
        "alert_id": f"alert-{rule.rule_id}-demo",
        "rule_id": rule.rule_id,
        "rule_version": rule.version,
        "user_id": "demo-user",
        "alert_type": rule.rule_type.value,
        "filters_json": dict(sensitivity.filters_json),
        "cooldown_seconds": sensitivity.cooldown_seconds,
        "channels": ["telegram"],
        "enabled": True,
    }


_LEGACY_TO_TYPE: dict[str, RuleType] = {
    "user_a_trader_position_updates": RuleType.TRADER_POSITION_UPDATE,
    "user_b_volume_spike": RuleType.VOLUME_SPIKE_5M,
    "user_c_new_market_liquidity": RuleType.NEW_MARKET_LIQUIDITY,
}


def _fallback_example_when_no_rules() -> dict[str, dict[str, Any]]:
    """Examples when ``ALARM_RULES_PATH`` is unset or empty at import time.

    Legacy template ids stay available for ``/templates`` and ``/create``; ids
    match ``deploy/config/rules.sample.json`` so a later whitelist lines up.
    """

    def _legacy_value(rule_id: str, alert_type: RuleType) -> dict[str, Any]:
        balanced = default_sensitivity_for(alert_type)
        return {
            "alert_id": f"alert-{rule_id}-demo",
            "rule_id": rule_id,
            "rule_version": 1,
            "user_id": "demo-user",
            "alert_type": alert_type.value,
            "filters_json": dict(balanced.filters_json),
            "cooldown_seconds": balanced.cooldown_seconds,
            "channels": ["telegram"],
            "enabled": True,
        }

    out: dict[str, dict[str, Any]] = {
        "example_minimal": {
            "summary": "Example (configure ALARM_RULES_PATH for real rule ids)",
            "value": _legacy_value("rule-volume-spike-default", RuleType.VOLUME_SPIKE_5M),
        },
    }
    legacy_ids = (
        (
            "user_a_trader_position_updates",
            "rule-trader-position-default",
            RuleType.TRADER_POSITION_UPDATE,
        ),
        (
            "user_b_volume_spike",
            "rule-volume-spike-default",
            RuleType.VOLUME_SPIKE_5M,
        ),
        (
            "user_c_new_market_liquidity",
            "rule-new-market-liquidity-default",
            RuleType.NEW_MARKET_LIQUIDITY,
        ),
    )
    for template_id, rule_id, atype in legacy_ids:
        out[template_id] = {
            "summary": f"Legacy alias ({atype.value})",
            "value": _legacy_value(rule_id, atype),
        }
    return out


def _build_alert_create_examples() -> dict[str, dict[str, Any]]:
    rules = load_rules_cached()
    if not rules:
        return _fallback_example_when_no_rules()

    examples: dict[str, dict[str, Any]] = {}
    first_by_type: dict[RuleType, AlertRuleV1] = {}
    for r in rules:
        if r.rule_type not in first_by_type:
            first_by_type[r.rule_type] = r

    for legacy_id, rtype in _LEGACY_TO_TYPE.items():
        rule = first_by_type.get(rtype)
        if rule is None:
            continue
        examples[legacy_id] = {
            "summary": rule.name,
            "value": _build_example_value(rule, default_sensitivity_for(rtype)),
        }

    for rule in rules:
        examples[rule.rule_id] = {
            "summary": rule.name,
            "value": _build_example_value(
                rule,
                default_sensitivity_for(rule.rule_type),
            ),
        }

    return examples


def get_alert_create_examples() -> dict[str, dict[str, Any]]:
    """Return current template catalog for ``/templates`` and ``/create``."""

    return _build_alert_create_examples()


class _AlertCreateExamplesProxy(Mapping[str, dict[str, Any]]):
    """Backward-compatible dynamic mapping for legacy imports.

    Some modules import ``ALERT_CREATE_EXAMPLES`` directly. Keep that symbol
    but route all reads to the current rules catalog.
    """

    def _current(self) -> dict[str, dict[str, Any]]:
        return get_alert_create_examples()

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._current()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())

    def get(
        self,
        key: str,
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._current().get(key, default)

    def items(self):
        return self._current().items()

    def keys(self):
        return self._current().keys()

    def values(self):
        return self._current().values()


ALERT_CREATE_EXAMPLES: Mapping[str, dict[str, Any]] = _AlertCreateExamplesProxy()


def build_alert_payload(
    *,
    rule_id: str,
    rule_version: int,
    alert_type: RuleType,
    sensitivity: SensitivityPreset | None = None,
    cooldown_seconds: int | None = None,
    filters_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble an ``AlertCreateRequest``-shaped dict for the wizard.

    Either ``sensitivity`` (preset path) or ``filters_json`` (custom path)
    must be provided. Default cooldown for the custom path comes from
    ``defaults.custom_path_cooldown_seconds`` in the presets JSON.
    """

    if filters_json is not None:
        fj = dict(filters_json)
        default_cd = DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS
    else:
        if sensitivity is None:
            raise ValueError("Either sensitivity or filters_json is required")
        fj = dict(sensitivity.filters_json)
        default_cd = sensitivity.cooldown_seconds
    return {
        "rule_id": rule_id,
        "rule_version": rule_version,
        "alert_type": alert_type.value,
        "filters_json": fj,
        "cooldown_seconds": (
            cooldown_seconds
            if cooldown_seconds is not None
            else default_cd
        ),
        "channels": ["telegram"],
        "enabled": True,
    }
