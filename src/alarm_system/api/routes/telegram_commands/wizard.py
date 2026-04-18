"""Interactive create-alert wizard.

State machine steps (``state["step"]``):

    "scenario"    -> user picks an alert scenario
    "sensitivity" -> user picks a noise profile
    "cooldown"    -> user picks or types a cooldown
    "preview"     -> user confirms and the alert is persisted

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
    lines = ["Шаг 1/4. Выберите сценарий алерта."]
    for scenario in _presets.SCENARIOS:
        lines.append(f"\n• {scenario.label}\n  {scenario.description}")
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_scenarios(_presets.scenario_menu_items()),
    )


def _step_sensitivity_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    lines = [
        "Шаг 2/4. Чувствительность.",
        f"Сценарий: {scenario.label}.",
        "",
        "• Тихо — меньше шума, самые сильные сигналы.",
        "• Обычно — рекомендуется.",
        "• Агрессивно — ловим слабые сигналы, больше уведомлений.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_sensitivity(),
    )


def _step_cooldown_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
    default_cd = state.get("cooldown_seconds", sensitivity.cooldown_seconds)
    lines = [
        "Шаг 3/4. Пауза между уведомлениями.",
        f"Сценарий: {scenario.label}.",
        f"Чувствительность: {sensitivity.label}.",
        f"Рекомендуется: {sensitivity.cooldown_seconds}s.",
        f"Выбрано сейчас: {default_cd}s.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_cooldown_presets(),
    )


def _step_preview_view(state: dict[str, Any]) -> CallbackResult:
    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
    cooldown = state["cooldown_seconds"]
    lines = [
        "Шаг 4/4. Проверьте параметры и подтвердите.",
        f"Сценарий: {scenario.label}",
        f"Тип: {scenario.alert_type.value}",
        f"Правило: {scenario.rule_id}#{scenario.rule_version}",
        f"Чувствительность: {sensitivity.label}",
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
        state["step"] = "scenario"
    elif step == "cooldown":
        state.pop("cooldown_seconds", None)
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
    state["sensitivity_id"] = preset.preset_id
    state["cooldown_seconds"] = preset.cooldown_seconds
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
    # Lazy import: alerts pulls in schemas which in turn loads the
    # whole presets module — fine as a lazy call, bad as a module-level
    # dependency of the wizard step renderers above.
    from alarm_system.api.routes.telegram_commands.alerts import (
        _create_alert_or_error,
    )

    scenario = _presets.SCENARIO_BY_ID[state["scenario_id"]]
    sensitivity = _presets.SENSITIVITY_BY_ID[state["sensitivity_id"]]
    payload = _presets.build_alert_payload(
        scenario=scenario,
        sensitivity=sensitivity,
        cooldown_seconds=state.get("cooldown_seconds"),
    )
    payload["user_id"] = ctx.user_id
    result = _create_alert_or_error(ctx, payload)

    if isinstance(result, str):
        # Creation failed (validation error, rule-identity rejection,
        # store conflict, etc.). Do NOT clear the session so the user
        # can correct the issue and try again from the preview step
        # rather than restarting the whole wizard.
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


async def handle_wizard_callback(
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
    if action == "wz_cd":
        return _handle_cooldown_preset(ctx, state, args)
    if action == "wz_cd_custom":
        return _handle_cooldown_custom(ctx)
    if action == "wz_confirm":
        return await _handle_confirm(ctx, state)
    return CallbackResult(toast="Кнопка устарела")


async def handle_wizard_text(
    ctx: CommandContext,
    text: str,
) -> CallbackResult | None:
    """Handle free-form text input while a wizard session is active.

    Only the 'custom cooldown' step currently expects text; any other
    message during an active wizard is coerced back to the current
    step's view so the user sees how to continue.
    """

    state = _load_state(ctx)
    if state is None:
        return None

    pending = _ui.get_pending_input(ctx)
    if pending is None or pending.get("kind") != "wizard_cooldown":
        return _render(state)

    parsed = _ui.parse_cooldown_value(text.strip())
    if isinstance(parsed, CallbackResult):
        # Keep the preset keyboard visible so the user can recover.
        return CallbackResult(
            text=parsed.toast or "Некорректное значение.",
            reply_markup=_keyboards.wizard_cooldown_presets(),
        )
    state["cooldown_seconds"] = parsed
    state["step"] = "preview"
    _save_state(ctx, state)
    _ui.clear_pending_input(ctx)
    return _render(state)
