"""Product-facing presets for the interactive alert-creation wizard.

This module is the single source of truth for:

- the human-readable catalog of scenarios shown by the wizard step 1;
- the ``Conservative``/``Balanced``/``Aggressive`` sensitivity bundles
  mapped from :doc:`docs/architecture/rules-dsl-v1.md`;
- the legacy ``ALERT_CREATE_EXAMPLES`` shape consumed by Swagger and
  the advanced ``/create`` slash command.

The wizard composes a scenario + sensitivity into a ready
``AlertCreateRequest`` payload; the slash command only needs the
legacy template bundle for backwards compatibility. Keeping both
derived from the same source guarantees the two code paths produce
equivalent alerts when the same scenario/sensitivity is picked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alarm_system.rules_dsl import RuleType


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
    """A named bundle of cooldown + filters defaults.

    The filters map is deliberately simple (string->scalar) so it
    serialises cleanly to ``AlertCreateRequest.filters_json`` without
    a dedicated conversion layer.
    """

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


SENSITIVITY_PRESETS: tuple[SensitivityPreset, ...] = (
    SensitivityPreset(
        preset_id="conservative",
        label="Тихо (Conservative)",
        cooldown_seconds=300,
        filters_json={
            "return_1m_pct_min": 2.0,
            "return_5m_pct_min": 4.0,
            "spread_bps_max": 80,
            "imbalance_abs_min": 0.30,
            "liquidity_usd_min": 250000,
        },
    ),
    SensitivityPreset(
        preset_id="balanced",
        label="Обычно (Balanced)",
        cooldown_seconds=180,
        filters_json={
            "return_1m_pct_min": 1.2,
            "return_5m_pct_min": 2.5,
            "spread_bps_max": 120,
            "imbalance_abs_min": 0.20,
            "liquidity_usd_min": 100000,
        },
    ),
    SensitivityPreset(
        preset_id="aggressive",
        label="Агрессивно (Aggressive)",
        cooldown_seconds=90,
        filters_json={
            "return_1m_pct_min": 0.7,
            "return_5m_pct_min": 1.5,
            "spread_bps_max": 180,
            "imbalance_abs_min": 0.12,
            "liquidity_usd_min": 50000,
        },
    ),
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


# Legacy ids kept alive for back-compat with existing automation,
# Swagger examples and integration tests that reference them. New
# clients should use the product ``scenario_id`` keys instead.
_LEGACY_ALIASES = {
    "user_a_trader_position_updates": "trader_positions",
    "user_b_iran_volume_spike": "volume_spike",
    "user_c_new_market_liquidity": "new_market_liquidity",
}


def _build_alert_create_examples() -> dict[str, dict[str, Any]]:
    balanced = SENSITIVITY_BY_ID["balanced"]
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


# Single source of truth, computed once at import time so downstream
# consumers (Swagger ``examples``, ``/templates``) all share the same
# dict identity and cannot drift.
ALERT_CREATE_EXAMPLES: dict[str, dict[str, Any]] = _build_alert_create_examples()


def build_alert_payload(
    *,
    scenario: Scenario,
    sensitivity: SensitivityPreset,
    cooldown_seconds: int | None = None,
) -> dict[str, Any]:
    """Assemble an ``AlertCreateRequest``-shaped dict for the wizard."""

    return {
        "rule_id": scenario.rule_id,
        "rule_version": scenario.rule_version,
        "alert_type": scenario.alert_type.value,
        "filters_json": dict(sensitivity.filters_json),
        "cooldown_seconds": (
            cooldown_seconds
            if cooldown_seconds is not None
            else sensitivity.cooldown_seconds
        ),
        "channels": ["telegram"],
        "enabled": True,
    }
