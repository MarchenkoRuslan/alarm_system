"""Interactive create-alert wizard.

State machine steps (``state["step"]``):

    "rule"           -> user picks a server rule (from active catalog)
    "sensitivity"    -> preset profile or custom filter thresholds
    "custom_filters" -> optional key=value line (only for custom path)
    "cooldown"       -> user picks or types a cooldown
    "preview"        -> user confirms and the alert is persisted

The wizard persists its state in the shared :class:`SessionStore`
under :data:`_ui.WIZARD_KEY`. The final step routes through the same
pipeline as slash commands so a wizard-built alert matches ``AlertCreateRequest``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from alarm_system.alert_filters import (
    parse_filter_kv_line,
    validated_filters_dict,
)
from alarm_system.api import alert_presets as _presets
from alarm_system.api.rule_catalog import (
    catalog_identity_hash,
    load_rules_cached,
    parse_rule_index,
    rule_at_index,
)
from alarm_system.api.routes.telegram_commands import _keyboards, _ui
from alarm_system.api.routes.telegram_commands._context import CommandContext
from alarm_system.api.routes.telegram_commands._ui import CallbackResult
from alarm_system.rules_dsl import RuleType


def _state_alert_type(state: dict[str, Any]) -> RuleType | None:
    raw = state.get("alert_type")
    if not isinstance(raw, str):
        return None
    try:
        return RuleType(raw)
    except ValueError:
        return None


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


def _rule_keyboard_rows() -> list[tuple[str, str]]:
    rules = load_rules_cached()
    rows: list[tuple[str, str]] = []
    for i, r in enumerate(rules):
        label = f"{r.name} ({r.rule_type.value})"
        if len(label) > 58:
            label = label[:55] + "…"
        rows.append((str(i), label))
    return rows


def _step_rule_view(_state: dict[str, Any]) -> CallbackResult:
    rules = load_rules_cached()
    if not rules:
        return CallbackResult(
            text=(
                "Каталог правил пуст. Опубликуйте активный набор правил "
                "в Postgres и повторите."
            ),
            reply_markup=_keyboards.back_home(),
        )
    lines = [
        "Шаг 1. Выберите правило из активного каталога сервера.",
        "Правило задаёт DSL и тип алерта; дальше настраиваются фильтры подписки.",
    ]
    for i, r in enumerate(rules):
        lines.append(f"\n{i}. {r.name}\n   {r.rule_type.value} — {r.rule_id}#{r.version}")
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_rules(_rule_keyboard_rows()),
    )


def _step_sensitivity_view(state: dict[str, Any]) -> CallbackResult:
    name = state.get("rule_name", "правило")
    alert_type = _state_alert_type(state)
    if alert_type is None:
        return CallbackResult(toast="Выберите правило заново")
    presets = _presets.sensitivity_presets_for(alert_type)
    sensitivity_buttons = [(p.preset_id, p.label) for p in presets]
    lines = [
        "Шаг 2. На каких рынках и при каких сигналах слать уведомления.",
        f"Правило: {name}.",
        "",
        "• Готовые профили — быстрый набор порогов из alert_presets.json.",
        "• Свои значения — на следующем шаге зададите теги и пороги вручную.",
    ]
    return CallbackResult(
        text="\n".join(lines),
        reply_markup=_keyboards.wizard_sensitivity(sensitivity_buttons),
    )


def _step_custom_filters_view(state: dict[str, Any]) -> CallbackResult:
    name = state.get("rule_name", "правило")
    lines = [
        "Шаг 3. Уточните охват и сигналы (дополнительно к правилу на сервере).",
        f"Правило: {name}.",
        "",
        "Рынки: category_tags=politics,crypto",
        "Сигналы: return_1m_pct_min=1.2 return_5m_pct_min=2.5 spread_bps_max=120 …",
        "Трейдеры: min_smart_score=85 min_account_age_days=365",
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
    name = state.get("rule_name", "правило")
    alert_type = _state_alert_type(state)
    if alert_type is None:
        return CallbackResult(toast="Выберите правило заново")
    if state.get("filter_mode") == "custom":
        sens_label = "Свои фильтры"
        rec_cd = _presets.DEFAULT_CUSTOM_PATH_COOLDOWN_SECONDS
        default_cd = state.get("cooldown_seconds", rec_cd)
        fj = state.get("custom_filters_json") or {}
        fj_line = f"Фильтры: {fj}" if fj else "Фильтры: (без дополнительных ограничений)"
    else:
        sensitivity = _presets.sensitivity_preset_for(
            alert_type,
            state["sensitivity_id"],
        )
        sens_label = sensitivity.label
        rec_cd = sensitivity.cooldown_seconds
        default_cd = state.get("cooldown_seconds", sensitivity.cooldown_seconds)
        fj_line = ""
    lines = [
        "Шаг: пауза между уведомлениями.",
        f"Правило: {name}.",
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
    name = state.get("rule_name", "")
    cooldown = state["cooldown_seconds"]
    alert_type = _state_alert_type(state)
    if alert_type is None:
        return CallbackResult(toast="Выберите правило заново")
    if state.get("filter_mode") == "custom":
        sens_label = "Свои фильтры"
        fj = state.get("custom_filters_json") or {}
    else:
        sensitivity = _presets.sensitivity_preset_for(
            alert_type,
            state["sensitivity_id"],
        )
        sens_label = sensitivity.label
        fj = sensitivity.filters_json
    lines = [
        "Проверьте параметры и подтвердите.",
        f"Правило: {name}",
        f"Тип: {state.get('alert_type', '')}",
        f"Идентификатор: {state.get('rule_id', '')}#{state.get('rule_version', '')}",
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
    "rule": _step_rule_view,
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


def _sync_catalog_or_reset(ctx: CommandContext, state: dict[str, Any]) -> CallbackResult | None:
    """If the active rule catalog changed, reset wizard to step ``rule``."""

    rules = load_rules_cached()
    h = catalog_identity_hash(rules)
    stored = state.get("rules_catalog_hash")
    if stored is None:
        state["rules_catalog_hash"] = h
        _save_state(ctx, state)
        return None
    if stored == h:
        return None
    state.clear()
    state["step"] = "rule"
    state["rules_catalog_hash"] = h
    _save_state(ctx, state)
    rendered = _render(state)
    return replace(
        rendered,
        toast="Список правил на сервере изменился — выберите снова.",
    )


async def start_wizard(ctx: CommandContext) -> CallbackResult:
    """Entry point for the ``/new`` command and the 'Создать' button."""

    rules = load_rules_cached()
    if not rules:
        return CallbackResult(
            text=(
                "Нет правил в активном каталоге. "
                "Опубликуйте active rule_set в Postgres и повторите."
            ),
            reply_markup=_keyboards.back_home(),
        )
    state = {
        "step": "rule",
        "rules_catalog_hash": catalog_identity_hash(rules),
    }
    _save_state(ctx, state)
    return _render(state)


def _handle_cancel(ctx: CommandContext) -> CallbackResult:
    _ui.clear_session(ctx)
    return CallbackResult(
        text="Создание отменено.",
        reply_markup=_keyboards.back_home(),
    )


def _handle_back(ctx: CommandContext, state: dict[str, Any]) -> CallbackResult:
    step = state.get("step")
    if step == "sensitivity":
        for k in ("rule_id", "rule_version", "alert_type", "rule_name"):
            state.pop(k, None)
        state.pop("filter_mode", None)
        state["step"] = "rule"
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


def _handle_rule_pick(
    ctx: CommandContext,
    state: dict[str, Any],
    args: list[str],
) -> CallbackResult:
    if not args:
        return CallbackResult(toast="Некорректный выбор")
    idx = parse_rule_index(args[0])
    rules = load_rules_cached()
    if idx is None:
        return CallbackResult(toast="Некорректный индекс")
    rule = rule_at_index(rules, idx)
    if rule is None:
        return CallbackResult(toast="Правило не найдено")
    state["rule_id"] = rule.rule_id
    state["rule_version"] = rule.version
    state["alert_type"] = rule.rule_type.value
    state["rule_name"] = rule.name
    state["step"] = "sensitivity"
    _save_state(ctx, state)
    return _render(state)


def _handle_sensitivity(
    ctx: CommandContext,
    state: dict[str, Any],
    args: list[str],
) -> CallbackResult:
    alert_type = _state_alert_type(state)
    if alert_type is None:
        return CallbackResult(toast="Сначала выберите правило")
    allowed = _presets.sensitivity_by_id_for(alert_type)
    if not args or args[0] not in allowed:
        return CallbackResult(toast="Неизвестная чувствительность")
    preset = allowed[args[0]]
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

    rid = state["rule_id"]
    rv = int(state["rule_version"])
    at = _state_alert_type(state)
    if at is None:
        return CallbackResult(toast="Мастер устарел, выберите правило заново")
    if state.get("filter_mode") == "custom":
        payload = _presets.build_alert_payload(
            rule_id=rid,
            rule_version=rv,
            alert_type=at,
            sensitivity=None,
            filters_json=dict(state.get("custom_filters_json") or {}),
            cooldown_seconds=state.get("cooldown_seconds"),
        )
    else:
        sensitivity = _presets.sensitivity_preset_for(
            at,
            state["sensitivity_id"],
        )
        payload = _presets.build_alert_payload(
            rule_id=rid,
            rule_version=rv,
            alert_type=at,
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


async def handle_wizard_callback(  # noqa: C901
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

    if state.get("step") == "scenario":
        state["step"] = "rule"
        state.pop("scenario_id", None)
        _save_state(ctx, state)

    reset = _sync_catalog_or_reset(ctx, state)
    if reset is not None:
        return reset

    state = _load_state(ctx)
    if state is None:
        return CallbackResult(toast="Мастер устарел, нажмите 'Создать алерт'")
    if action == "wz_back":
        return _handle_back(ctx, state)
    if action == "wz_rule":
        return _handle_rule_pick(ctx, state, args)
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
    at = _state_alert_type(state)
    if at is None:
        return CallbackResult(
            text="Мастер устарел, выберите правило заново.",
            reply_markup=_keyboards.back_home(),
        )
    raw = parse_filter_kv_line(text.strip())
    try:
        validated = validated_filters_dict(at, raw)
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
