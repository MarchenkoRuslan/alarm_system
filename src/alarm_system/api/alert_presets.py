"""Product-facing presets for the interactive alert-creation wizard.

Sensitivity bundles and default cooldowns load from
``deploy/config/alert_presets.json`` (override path via
``ALARM_ALERT_PRESETS_PATH``). If the file is missing or invalid, embedded
defaults are used so tests and minimal installs keep working.

The wizard composes a scenario + sensitivity into a ready
``AlertCreateRequest`` payload; the slash command uses
``ALERT_CREATE_EXAMPLES`` for backwards compatibility.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alarm_system.rules_dsl import RuleType

_PRESETS_FILE_ENV = "ALARM_ALERT_PRESETS_PATH"


@dataclass(frozen=True)
class Scenario:
    """One product-facing alert scenario the user can pick in the wizard."""

    scenario_id: str
    label: str
    description: str
    alert_type: RuleType
    rule_id: str
    rule_version: int = 1


@dataclass(frozen=True)
class SensitivityPreset:
    """A named bundle of cooldown + filters defaults."""

    preset_id: str
    label: str
    cooldown_seconds: int
    filters_json: dict[str, Any]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="trader_positions",
        label="Сделки топ-трейдеров",
        description=(
            "Уведомления об открытии/закрытии/изменении позиций "
            "крупными трейдерами. Фильтр по качеству трейдера и "
            "категориям рынков."
        ),
        alert_type=RuleType.TRADER_POSITION_UPDATE,
        rule_id="rule-trader-position-default",
    ),
    Scenario(
        scenario_id="volume_spike",
        label="Всплеск объёма за 5 минут",
        description=(
            "Ловит резкий рост объёма торгов на рынке за окно в "
            "5 минут. Полезно для реакции на новости."
        ),
        alert_type=RuleType.VOLUME_SPIKE_5M,
        rule_id="rule-volume-spike-default",
    ),
    Scenario(
        scenario_id="new_market_liquidity",
        label="Новые рынки с ликвидностью",
        description=(
            "Срабатывает, когда у нового рынка растёт ликвидность "
            "выше порога. Позволяет первыми заходить в свежие рынки."
        ),
        alert_type=RuleType.NEW_MARKET_LIQUIDITY,
        rule_id="rule-new-market-liquidity-default",
    ),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _default_presets_path() -> Path:
    return _repo_root() / "deploy" / "config" / "alert_presets.json"


def _embedded_json_defaults() -> dict[str, Any]:
    """Fallback when JSON file is absent (tests, sdist without deploy/)."""

    return {
        "defaults": {"custom_path_cooldown_seconds": 180},
        "sensitivity_presets": [
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


def _parse_sensitivity_presets(blob: dict[str, Any]) -> tuple[SensitivityPreset, ...]:
    raw_list = blob.get("sensitivity_presets")
    if not isinstance(raw_list, list) or not raw_list:
        raw_list = _embedded_json_defaults()["sensitivity_presets"]
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
    if not out:
        return tuple(
            SensitivityPreset(
                preset_id=p["preset_id"],
                label=p["label"],
                cooldown_seconds=p["cooldown_seconds"],
                filters_json=dict(p["filters_json"]),
            )
            for p in _embedded_json_defaults()["sensitivity_presets"]
        )
    return tuple(out)


def _parse_custom_default_cooldown(blob: dict[str, Any]) -> int:
    defaults = blob.get("defaults")
    if isinstance(defaults, dict):
        raw = defaults.get("custom_path_cooldown_seconds")
        if isinstance(raw, int) and raw >= 0:
            return raw
    return int(_embedded_json_defaults()["defaults"]["custom_path_cooldown_seconds"])


_PRESETS_BLOB = _load_presets_blob()
SENSITIVITY_PRESETS: tuple[SensitivityPreset, ...] = _parse_sensitivity_presets(
    _PRESETS_BLOB
)
DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS: int = _parse_custom_default_cooldown(
    _PRESETS_BLOB
)

SCENARIO_BY_ID: dict[str, Scenario] = {s.scenario_id: s for s in SCENARIOS}
SENSITIVITY_BY_ID: dict[str, SensitivityPreset] = {
    p.preset_id: p for p in SENSITIVITY_PRESETS
}


def scenario_menu_items() -> list[tuple[str, str]]:
    """``(scenario_id, label)`` list for the wizard keyboard."""

    return [(s.scenario_id, s.label) for s in SCENARIOS]


def _build_example_value(scenario: Scenario, sensitivity: SensitivityPreset) -> dict[str, Any]:
    return {
        "alert_id": f"alert-{scenario.scenario_id}-demo",
        "rule_id": scenario.rule_id,
        "rule_version": scenario.rule_version,
        "user_id": "demo-user",
        "alert_type": scenario.alert_type.value,
        "filters_json": dict(sensitivity.filters_json),
        "cooldown_seconds": sensitivity.cooldown_seconds,
        "channels": ["telegram"],
        "enabled": True,
    }


_LEGACY_ALIASES = {
    "user_a_trader_position_updates": "trader_positions",
    "user_b_volume_spike": "volume_spike",
    "user_c_new_market_liquidity": "new_market_liquidity",
}


def _build_alert_create_examples() -> dict[str, dict[str, Any]]:
    balanced = SENSITIVITY_BY_ID.get("balanced") or SENSITIVITY_PRESETS[0]
    examples: dict[str, dict[str, Any]] = {}
    for legacy_id, scenario_id in _LEGACY_ALIASES.items():
        scenario = SCENARIO_BY_ID[scenario_id]
        examples[legacy_id] = {
            "summary": scenario.label,
            "value": _build_example_value(scenario, balanced),
        }
    for scenario in SCENARIOS:
        examples[scenario.scenario_id] = {
            "summary": scenario.label,
            "value": _build_example_value(scenario, balanced),
        }
    return examples


ALERT_CREATE_EXAMPLES: dict[str, dict[str, Any]] = _build_alert_create_examples()


def build_alert_payload(
    *,
    scenario: Scenario,
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
        "rule_id": scenario.rule_id,
        "rule_version": scenario.rule_version,
        "alert_type": scenario.alert_type.value,
        "filters_json": fj,
        "cooldown_seconds": (
            cooldown_seconds
            if cooldown_seconds is not None
            else default_cd
        ),
        "channels": ["telegram"],
        "enabled": True,
    }
