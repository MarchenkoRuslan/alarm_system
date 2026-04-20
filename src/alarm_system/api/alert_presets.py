"""Sensitivity bundles and defaults for the alert-creation wizard.

Profiles load from ``deploy/config/alert_presets.json`` (override via
``ALARM_ALERT_PRESETS_PATH``). Template ids come only from live rule catalog
entries exposed by :mod:`alarm_system.api.rule_catalog`.
"""

from __future__ import annotations

import json
import os
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


@dataclass(frozen=True)
class _PresetConfig:
    presets_by_type: dict[RuleType, tuple[SensitivityPreset, ...]]
    by_id_by_type: dict[RuleType, dict[str, SensitivityPreset]]
    default_presets: tuple[SensitivityPreset, ...]
    default_by_id: dict[str, SensitivityPreset]
    default_custom_path_cooldown_seconds: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _default_presets_path() -> Path:
    return _repo_root() / "deploy" / "config" / "alert_presets.json"


def _load_presets_blob() -> dict[str, Any]:
    raw_path = os.getenv(_PRESETS_FILE_ENV)
    path = Path(raw_path) if raw_path else _default_presets_path()
    if not path.is_file():
        raise ValueError(f"Presets file is required and was not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to load presets file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Presets file must be a JSON object")
    return payload


def _parse_one_sensitivity_list(raw_list: object) -> tuple[SensitivityPreset, ...]:
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError("Sensitivity presets must be a non-empty list")
    out: list[SensitivityPreset] = []
    for item in raw_list:
        if not isinstance(item, dict):
            raise ValueError("Each preset entry must be an object")
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
            raise ValueError(
                "Each preset must define preset_id, label, cooldown_seconds, "
                "and filters_json."
            )
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
    raw = blob.get("sensitivity_presets")
    if not isinstance(raw, dict):
        raise ValueError("sensitivity_presets must be an object keyed by rule_type")
    parsed_by_type: dict[RuleType, tuple[SensitivityPreset, ...]] = {}
    for rule_type in RuleType:
        parsed_by_type[rule_type] = _parse_one_sensitivity_list(
            raw.get(rule_type.value)
        )
    return parsed_by_type


def _parse_custom_default_cooldown(blob: dict[str, Any]) -> int:
    defaults = blob.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError("defaults object is required in presets file")
    raw = defaults.get("custom_path_cooldown_seconds")
    if not isinstance(raw, int) or raw < 0:
        raise ValueError("defaults.custom_path_cooldown_seconds must be int >= 0")
    return raw


def _preset_config() -> _PresetConfig:
    blob = _load_presets_blob()
    presets_by_type = _parse_sensitivity_presets(blob)
    by_id_by_type: dict[RuleType, dict[str, SensitivityPreset]] = {
        rule_type: {preset.preset_id: preset for preset in presets}
        for rule_type, presets in presets_by_type.items()
    }
    default_presets = presets_by_type[RuleType.VOLUME_SPIKE_5M]
    default_by_id = by_id_by_type[RuleType.VOLUME_SPIKE_5M]
    return _PresetConfig(
        presets_by_type=presets_by_type,
        by_id_by_type=by_id_by_type,
        default_presets=default_presets,
        default_by_id=default_by_id,
        default_custom_path_cooldown_seconds=_parse_custom_default_cooldown(blob),
    )


def sensitivity_presets_for(alert_type: RuleType) -> tuple[SensitivityPreset, ...]:
    cfg = _preset_config()
    return cfg.presets_by_type.get(alert_type, cfg.default_presets)


def sensitivity_by_id_for(alert_type: RuleType) -> dict[str, SensitivityPreset]:
    cfg = _preset_config()
    return cfg.by_id_by_type.get(alert_type, cfg.default_by_id)


def sensitivity_preset_for(
    alert_type: RuleType,
    preset_id: str,
) -> SensitivityPreset:
    return sensitivity_by_id_for(alert_type)[preset_id]


def default_sensitivity_for(alert_type: RuleType) -> SensitivityPreset:
    by_id = sensitivity_by_id_for(alert_type)
    return by_id.get("balanced") or next(iter(by_id.values()))


def custom_path_cooldown_seconds() -> int:
    return _preset_config().default_custom_path_cooldown_seconds


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


def _build_alert_create_examples() -> dict[str, dict[str, Any]]:
    rules = load_rules_cached()
    if not rules:
        raise ValueError(
            "Rule catalog is empty or unavailable. Configure ALARM_RULES_PATH "
            "or ALARM_USE_DATABASE_RULES to expose templates."
        )

    examples: dict[str, dict[str, Any]] = {}
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
        default_cd = custom_path_cooldown_seconds()
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
