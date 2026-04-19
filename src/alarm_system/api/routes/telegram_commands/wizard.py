"""Interactive create-alert wizard.

State machine steps (``state["step"]``):

    "scenario"       -> user picks an alert scenario
    "sensitivity"    -> preset profile or custom filter thresholds
    "custom_filters" -> optional key=value line (only for custom path)
    "cooldown"       -> user picks or types a cooldown
    "preview"        -> user confirms and the alert is persisted

The wizard persists its state in the shared :class:`SessionStore`
under :data:`_ui.WIZARD_KEY`, so the callback dispatcher and the
"pending text input" flow can both resume the same conversation
without additional wiring. On any step the user can navigate back or
cancel, which clears the session.

The final step routes through the same payload pipeline as the
``/create_raw`` handler (``_create_from_payload`` in ``alerts_write``)
so a wizard-built alert is indistinguishable from a JSON-submitted one.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from alarm_system.alert_filters import (
    parse_filter_kv_line,
    validated_filters_dict,
)
from alarm_system.api import alert_presets as _presets
from alarm_system.api.routes.telegram_commands import _keyboards, _ui
from alarm_system.api.routes.telegram_commands._context import CommandContext
from alarm_system.api.routes.telegram_commands._ui import CallbackResult


# --------------------------------------------------------------------------
# State helpers.
# --------------------------------------------------------------------------


def _load_state(ctx: CommandContext) -> dict[str, Any] | None:
    session = ctx.session_store.load(ctx.user_id)
    if not session:
        return None
    state = session.get(_ui.WIZARD_KEY)
    return state if isinstance(state, dict) else None


def _save_state(ctx: CommandContext, state: dict[str, Any]) -> None:
    session = _ui.load_session(ctx)
    session[_ui.WIZARD_KEY] = state
    session.pop(_ui.PENDING_INPUT_KEY, None)
    _ui.save_session(ctx, session)


# --------------------------------------------------------------------------
# Step renderers — pure functions of ``state`` returning CallbackResult.
# --------------------------------------------------------------------------


def _step_scenario_view() -> CallbackResult:
    lines = [
        "Шаг 1. Что отслеживать — тип событий и правило на сервере.",
        "Ниже — готовые сценарии (позиции, всплеск объёма, ликвидность новых рынков).",
    ]
    for scenario in _presets.SCENARIOS:
        lines.append(f"\n• {scenario.label}\n  {scenario.description}")
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_scenarios(_presets.scenario_menu_items()),
    )


def _step_sensitivity_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    lines = [
        "Шаг 2. На каких рынках и при каких сигналах слать уведомления.",
        f"Сценарий: {scenario.label}.",
        "",
        "• Готовые профили — быстрый набор порогов по цене, спреду, ликвидности, дисбалансу стакана.",
        "• Свои значения — на следующем шаге зададите теги рынков и пороги сигналов вручную.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_sensitivity(),
    )


def _step_custom_filters_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    lines = [
        "Шаг 3. Уточните охват и сигналы (дополнительно к правилу на сервере).",
        f"Сценарий: {scenario.label}.",
        "",
        "Рынки: category_tags=politics,crypto — только события с пересечением тегов.",
        "Цена/ликвидность/стакан: return_1m_pct_min=1.2 return_5m_pct_min=2.5",
        "spread_bps_max=120 imbalance_abs_min=0.2 liquidity_usd_min=100000",
        "Трейдеры (если сценарий про позиции): min_smart_score=85 min_account_age_days=365",
        "Новые рынки: target_liquidity_usd=150000 deferred_watch_ttl_hours=336",
        "",
        "Отправьте одним сообщением пары key=value через пробел.",
        "Или «Пропустить» — без дополнительных ограничений.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_custom_filters(),
    )


def _step_cooldown_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    if state.get("filter_mode") == "custom":
        sens_label = "Свои фильтры"
        rec_cd = _presets.DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS
        default_cd = state.get("cooldown_seconds", rec_cd)
        fj = state.get("custom_filters_json") or {}
        fj_line = f"Фильтры: {fj}" if fj else "Фильтры: (без дополнительных ограничений)"
    else:
        sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
        sens_label = sensitivity.label
        rec_cd = sensitivity.cooldown_seconds
        default_cd = state.get("cooldown_seconds", sensitivity.cooldown_seconds)
        fj_line = ""
    lines = [
        "Шаг: пауза между уведомлениями.",
        f"Сценарий: {scenario.label}.",
        f"Профиль: {sens_label}.",
        fj_line,
        f"Рекомендуется: {rec_cd}s.",
        f"Выбрано сейчас: {default_cd}s.",
    ]
    lines = [ln for ln in lines if ln != ""]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_cooldown_presets(),
    )


def _step_preview_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    cooldown = state["cooldown_seconds"]
    if state.get("filter_mode") == "custom":
        sens_label = "Свои фильтры"
        fj = state.get("custom_filters_json") or {}
    else:
        sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
        sens_label = sensitivity.label
        fj = sensitivity.filters_json
    lines = [
        "Проверьте параметры и подтвердите.",
        f"Сценарий: {scenario.label}",
        f"Тип: {scenario.alert_type.value}",
        f"Правило: {scenario.rule_id}#{scenario.rule_version}",
        f"Профиль: {sens_label}",
        f"Фильтры: {fj}",
        f"Cooldown: {cooldown}s",
        "",
        "Нажмите 'Создать алерт', чтобы сохранить.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_preview(),
    )


_RENDERERS = {
    "scenario": lambda _state: _step_scenario_view(),
    "sensitivity": _step_sensitivity_view,
    "custom_filters": _step_custom_filters_view,
    "cooldown": _step_cooldown_view,
    "preview": _step_preview_view,
}


def _render(state: dict[str, Any]) -> CallbackResult:
    renderer = _RENDERERS.get(state.get("step", ""))
    if renderer is None:
        return CallbackResult(toast="Мастер устарел, начните заново")
    return renderer(state)


# --------------------------------------------------------------------------
# Public entry points.
# --------------------------------------------------------------------------


async def start_wizard(ctx: CommandContext) -> CallbackResult:
    """Entry point for the ``/new`` command and the 'Создать' button."""

    state = {"step": "scenario"}
    _save_state(ctx, state)
    return _render(state)


# --------------------------------------------------------------------------
# Action handlers. Each one mutates the state dict in place + persists.
# --------------------------------------------------------------------------


def _handle_cancel(ctx: CommandContext) -> CallbackResult:
    _ui.clear_session(ctx)
    return CallbackResult(
        text="Создание отменено.",
        reply_markup=_keyboards.back_home(),
    )


def _handle_back(ctx: CommandContext, state: dict[str, Any]) -> CallbackResult:
    step = state.get("step")
    if step == "sensitivity":
        state.pop("scenario_id", None)
        state.pop("filter_mode", None)
        state["step"] = "scenario"
    elif step == "custom_filters":
        state.pop("custom_filters_json", None)
        state.pop("filter_mode", None)
        state["step"] = "sensitivity"
    elif step == "cooldown":
        state.pop("cooldown_seconds", None)
        if state.get("filter_mode") == "custom":
            state["step"] = "custom_filters"
        else:
            state["step"] = "sensitivity"
    elif step == "preview":
        state["step"] = "cooldown"
    _save_state(ctx, state)
    return _render(state)


def _handle_scenario(
    ctx: CommandContext,
    state: dict[str, Any],
    args: list[str],
) -> CallbackResult:
    if not args or args[0] not in _presets.SCENARIO_BY_ID:
        return CallbackResult(toast="Неизвестный сценарий")
    state["scenario_id"] = args[0]
    state["step"] = "sensitivity"
    _save_state(ctx, state)
    return _render(state)


def _handle_sensitivity(
    ctx: CommandContext,
    state: dict[str, Any],
    args: list[str],
) -> CallbackResult:
    if not args or args[0] not in _presets.SENSITIVITY_BY_ID:
        return CallbackResult(toast="Неизвестная чувствительность")
    preset = _presets.SENSITIVITY_BY_ID[args[0]]
    state["filter_mode"] = "preset"
    state["sensitivity_id"] = preset.preset_id
    state["cooldown_seconds"] = preset.cooldown_seconds
    state.pop("custom_filters_json", None)
    state["step"] = "cooldown"
    _save_state(ctx, state)
    return _render(state)


def _handle_filters_custom(
    ctx: CommandContext,
    state: dict[str, Any],
) -> CallbackResult:
    state["filter_mode"] = "custom"
    state.pop("sensitivity_id", None)
    state["step"] = "custom_filters"
    state["custom_filters_json"] = {}
    _save_state(ctx, state)
    return _render(state)


def _handle_filters_skip(
    ctx: CommandContext,
    state: dict[str, Any],
) -> CallbackResult:
    if state.get("step") != "custom_filters":
        return CallbackResult(toast="Шаг устарел")
    state["custom_filters_json"] = {}
    state["cooldown_seconds"] = _presets.DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS
    state["step"] = "cooldown"
    _save_state(ctx, state)
    return _render(state)


def _handle_cooldown_preset(
    ctx: CommandContext,
    state: dict[str, Any],
    args: list[str],
) -> CallbackResult:
    if not args:
        return CallbackResult(toast="Кнопка устарела")
    parsed = _ui.parse_cooldown_value(args[0])
    if isinstance(parsed, CallbackResult):
        return parsed
    state["cooldown_seconds"] = parsed
    state["step"] = "preview"
    _save_state(ctx, state)
    return _render(state)


def _handle_cooldown_custom(ctx: CommandContext) -> CallbackResult:
    _ui.set_pending_input(ctx, kind="wizard_cooldown")
    return CallbackResult(
        text=(
            "Отправьте число секунд (0..86400) сообщением — это "
            "будет cooldown для нового алерта."
        ),
        reply_markup=_keyboards.wizard_cooldown_presets(),
    )


async def _handle_confirm(
    ctx: CommandContext,
    state: dict[str, Any],
) -> CallbackResult:
    if state.get("step") != "preview":
        return CallbackResult(toast="Завершите все шаги мастера")
    return await _finalise(ctx, state)


async def _finalise(
    ctx: CommandContext,
    state: dict[str, Any],
) -> CallbackResult:
    from alarm_system.api.routes.telegram_commands.alerts import (
        _create_alert_or_error,
    )

    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    if state.get("filter_mode") == "custom":
        payload = _presets.build_alert_payload(
            scenario=scenario,
            sensitivity=None,
            filters_json=dict(state.get("custom_filters_json") or {}),
            cooldown_seconds=state.get("cooldown_seconds"),
        )
    else:
        sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
        payload = _presets.build_alert_payload(
            scenario=scenario,
            sensitivity=sensitivity,
            cooldown_seconds=state.get("cooldown_seconds"),
        )
    payload["user_id"] = ctx.user_id
    result = _create_alert_or_error(ctx, payload)

    if isinstance(result, str):
        return CallbackResult(
            text=result,
            reply_markup=_keyboards.wizard_preview(),
        )

    _ui.clear_session(ctx)
    return CallbackResult(
        text=(
            f"Алерт создан: {result.alert_id}\n"
            f"Тип: {result.alert_type.value}\n"
            f"Cooldown: {result.cooldown_seconds}s"
        ),
        reply_markup=_keyboards.back_home(),
        toast="Алерт создан",
    )


async def handle_wizard_callback(  # noqa: C901 — thin dispatch; branches map 1:1 to UI
    action: str,
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    """Callback branch invoked by :mod:`_callbacks.dispatch_callback`."""

    state = _load_state(ctx)
    if state is None:
        return CallbackResult(toast="Мастер устарел, нажмите 'Создать алерт'")

    if action == "wz_cancel":
        return _handle_cancel(ctx)
    if action == "wz_back":
        return _handle_back(ctx, state)
    if action == "wz_scn":
        return _handle_scenario(ctx, state, args)
    if action == "wz_sens":
        return _handle_sensitivity(ctx, state, args)
    if action == "wz_filters_custom":
        return _handle_filters_custom(ctx, state)
    if action == "wz_filters_skip":
        return _handle_filters_skip(ctx, state)
    if action == "wz_cd":
        return _handle_cooldown_preset(ctx, state, args)
    if action == "wz_cd_custom":
        return _handle_cooldown_custom(ctx)
    if action == "wz_confirm":
        return await _handle_confirm(ctx, state)
    return CallbackResult(toast="Кнопка устарела")


def _handle_custom_filters_text(
    ctx: CommandContext,
    state: dict[str, Any],
    text: str,
) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    raw = parse_filter_kv_line(text.strip())
    try:
        validated = validated_filters_dict(scenario.alert_type, raw)
    except ValidationError as exc:
        return CallbackResult(
            text=(
                "Не удалось применить фильтры:\n"
                f"{exc}\n\n"
                "Проверьте имена полей и числа, затем отправьте снова."
            ),
            reply_markup=_keyboards.wizard_custom_filters(),
        )
    state["custom_filters_json"] = validated
    state["cooldown_seconds"] = _presets.DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS
    state["step"] = "cooldown"
    _save_state(ctx, state)
    return _render(state)


async def handle_wizard_text(
    ctx: CommandContext,
    text: str,
) -> CallbackResult | None:
    """Handle free-form text input while a wizard session is active."""

    state = _load_state(ctx)
    if state is None:
        return None

    if state.get("step") == "custom_filters":
        return _handle_custom_filters_text(ctx, state, text)

    pending = _ui.get_pending_input(ctx)
    if pending is None or pending.get("kind") != "wizard_cooldown":
        return _render(state)

    parsed = _ui.parse_cooldown_value(text.strip())
    if isinstance(parsed, CallbackResult):
        return CallbackResult(
            text=parsed.toast or "Некорректное значение.",
            reply_markup=_keyboards.wizard_cooldown_presets(),
        )
    state["cooldown_seconds"] = parsed
    state["step"] = "preview"
    _save_state(ctx, state)
    _ui.clear_pending_input(ctx)
    return _render(state)
