"""Inline-button callback handlers.

The Telegram webhook dispatcher receives ``callback_query`` updates
from tapped inline buttons, parses the ``callback_data`` payload with
:func:`_keyboards.parse_callback`, and routes to one of the handlers
registered here.

Each handler returns a :class:`CallbackResult` that the dispatcher
renders either by editing the existing message (normal flow) or by
answering the callback query with a short toast (quick
acknowledgements and errors).

Session access, alert-token resolution, rendering and optimistic-
update plumbing all live in :mod:`_ui`, so these handlers stay thin
and read as a flat "what the button does" list.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.routes.telegram_commands import (
    _keyboards,
    _ui,
    wizard,
)
from alarm_system.api.routes.telegram_commands._context import (
    BackendError,
    CommandContext,
)
from alarm_system.api.routes.telegram_commands._ui import CallbackResult
from alarm_system.entities import Alert


CallbackHandler = Callable[[CommandContext, list[str]], Awaitable[CallbackResult]]


# --------------------------------------------------------------------------
# Home / help / status / mute menu callbacks.
# --------------------------------------------------------------------------


async def _handle_home(ctx: CommandContext, _args: list[str]) -> CallbackResult:
    _ui.clear_session(ctx)
    return CallbackResult(
        text=(
            "Главное меню.\n"
            "Выберите действие кнопкой ниже или отправьте /help."
        ),
        reply_markup=_keyboards.home_menu(),
    )


async def _handle_help(_ctx: CommandContext, _args: list[str]) -> CallbackResult:
    # Lazy import to avoid a circular dependency with the registry.
    from alarm_system.api.routes.telegram_commands._registry import (
        build_help_text,
    )

    return CallbackResult(
        text=build_help_text(),
        reply_markup=_keyboards.back_home(),
    )


async def _handle_status(ctx: CommandContext, _args: list[str]) -> CallbackResult:
    from alarm_system.api.routes.telegram_commands.service import handle_status

    text = await handle_status(ctx)
    return CallbackResult(text=text, reply_markup=_keyboards.back_home())


async def _handle_mute_menu(
    _ctx: CommandContext,
    _args: list[str],
) -> CallbackResult:
    return CallbackResult(
        text="Выберите длительность тишины:",
        reply_markup=_keyboards.mute_menu(),
    )


async def _handle_mute_set(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    from alarm_system.api.routes.telegram_commands._args import (
        format_duration_seconds,
        parse_duration_seconds,
    )

    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    try:
        seconds = parse_duration_seconds(token)
    except ValueError as exc:
        return CallbackResult(toast=f"Некорректный интервал: {exc}")
    active_until = ctx.mute_store.set_mute(user_id=ctx.user_id, seconds=seconds)
    return CallbackResult(
        text=(
            f"Тишина включена на {format_duration_seconds(seconds)}.\n"
            f"До {active_until.isoformat(timespec='seconds')}."
        ),
        reply_markup=_keyboards.back_home(),
    )


async def _handle_unmute(
    ctx: CommandContext,
    _args: list[str],
) -> CallbackResult:
    cleared = ctx.mute_store.clear_mute(ctx.user_id)
    text = (
        "Тишина снята. Буду снова присылать алерты."
        if cleared
        else "Тишина и так не была включена."
    )
    return CallbackResult(text=text, reply_markup=_keyboards.back_home())


# --------------------------------------------------------------------------
# Alerts list + single-alert card.
# --------------------------------------------------------------------------


def _alerts_page_text(page: int, total_pages: int, total: int) -> str:
    if total == 0:
        return (
            "Активных алертов нет. Нажмите 'Создать алерт' "
            "или запустите мастер /new."
        )
    return (
        f"Ваши активные алерты (страница {page + 1} из {max(total_pages, 1)}, "
        f"всего {total}). Нажмите карточку для действий."
    )


def _alert_list_item(idx: int, alert: Alert) -> tuple[str, str]:
    status = "вкл" if alert.enabled else "выкл"
    label = (
        f"{alert.alert_type.value} · {status} · "
        f"cd {alert.cooldown_seconds}s"
    )
    return f"{idx:02d}", label


async def _handle_alerts_page(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    try:
        page = int(args[0]) if args else 0
    except ValueError:
        page = 0
    page = max(0, page)
    try:
        # Mirror /alerts behaviour: the interactive path always shows
        # only active alerts so callback navigation and slash command
        # totals stay consistent.
        alerts = ctx.store.list_alerts(
            user_id=ctx.user_id,
            include_disabled=False,
        )
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc

    total = len(alerts)
    page_size = _keyboards.ALERTS_PAGE_SIZE
    total_pages = (total + page_size - 1) // page_size if total else 1
    page = min(page, max(total_pages - 1, 0))
    start = page * page_size
    visible = alerts[start:start + page_size]
    _ui.store_alert_tokens(ctx, [a.alert_id for a in visible])

    items = [_alert_list_item(idx, alert) for idx, alert in enumerate(visible)]
    return CallbackResult(
        text=_alerts_page_text(page, total_pages, total),
        reply_markup=_keyboards.alerts_list(
            page=page,
            total_pages=total_pages,
            items=items,
        ),
    )


async def _handle_alert_card(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    resolved = _ui.resolve_card_alert(ctx, token)
    if isinstance(resolved, CallbackResult):
        return resolved
    return _ui.alert_card_view(token=token, alert=resolved)


# --------------------------------------------------------------------------
# Toggle enable/disable and cooldown changes on a card.
# --------------------------------------------------------------------------


async def _toggle_enabled(
    ctx: CommandContext,
    args: list[str],
    *,
    target: bool,
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    resolved = _ui.resolve_card_alert(ctx, token)
    if isinstance(resolved, CallbackResult):
        return resolved
    if resolved.enabled is target:
        return _ui.alert_card_view(
            token=token,
            alert=resolved,
            toast="Уже включен" if target else "Уже выключен",
        )
    saved = _ui.persist_alert_update(
        ctx,
        alert=resolved,
        updates={"enabled": target},
    )
    if isinstance(saved, CallbackResult):
        return saved
    return _ui.alert_card_view(
        token=token,
        alert=saved,
        toast="Включен" if target else "Выключен",
    )


async def _handle_alert_enable(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    return await _toggle_enabled(ctx, args, target=True)


async def _handle_alert_disable(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    return await _toggle_enabled(ctx, args, target=False)


async def _handle_alert_cooldown(
    _ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    return CallbackResult(
        text=(
            "Выберите cooldown или нажмите 'Другое значение', "
            "чтобы ввести число секунд сообщением."
        ),
        reply_markup=_keyboards.cooldown_options(token),
    )


async def _handle_alert_cooldown_set(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    if len(args) < 2:
        return CallbackResult(toast="Кнопка устарела")
    return await _apply_cooldown(ctx, token=args[0], raw_value=args[1])


async def _handle_alert_cooldown_custom(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    _ui.set_pending_input(ctx, kind="alert_cooldown", token=token)
    return CallbackResult(
        text=(
            "Отправьте число секунд (0..86400) сообщением. "
            "Нажмите 'Отмена', чтобы выйти."
        ),
        reply_markup=_keyboards.cooldown_options(token),
    )


async def _apply_cooldown(
    ctx: CommandContext,
    *,
    token: str,
    raw_value: str,
) -> CallbackResult:
    parsed = _ui.parse_cooldown_value(raw_value)
    if isinstance(parsed, CallbackResult):
        return parsed
    value = parsed
    resolved = _ui.resolve_card_alert(ctx, token)
    if isinstance(resolved, CallbackResult):
        return resolved
    if resolved.cooldown_seconds == value:
        return _ui.alert_card_view(
            token=token,
            alert=resolved,
            toast=f"Cooldown уже {value}s",
        )
    saved = _ui.persist_alert_update(
        ctx,
        alert=resolved,
        updates={"cooldown_seconds": value},
    )
    if isinstance(saved, CallbackResult):
        return saved
    _ui.clear_pending_input(ctx)
    return _ui.alert_card_view(
        token=token,
        alert=saved,
        toast=f"Cooldown = {value}s",
    )


# --------------------------------------------------------------------------
# Delete with confirmation.
# --------------------------------------------------------------------------


async def _handle_alert_delete(
    _ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    return CallbackResult(
        text="Удалить алерт? Действие необратимо.",
        reply_markup=_keyboards.confirm_delete(token),
    )


async def _handle_alert_delete_yes(
    ctx: CommandContext,
    args: list[str],
) -> CallbackResult:
    token, error = _ui.require_first_arg(args)
    if error is not None:
        return error
    assert token is not None
    resolved = _ui.resolve_card_alert(ctx, token)
    if isinstance(resolved, CallbackResult):
        return resolved
    try:
        deleted = ctx.store.delete_alert(resolved.alert_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    text = (
        f"Алерт {resolved.alert_id} удален." if deleted
        else f"Алерт {resolved.alert_id} уже был удален."
    )
    return CallbackResult(
        text=text,
        reply_markup=_keyboards.back_home(),
        toast="Удалено" if deleted else None,
    )


# --------------------------------------------------------------------------
# Wizard entry + registry.
# --------------------------------------------------------------------------


async def _handle_noop(
    _ctx: CommandContext,
    _args: list[str],
) -> CallbackResult:
    return CallbackResult()


async def _handle_new(ctx: CommandContext, _args: list[str]) -> CallbackResult:
    return await wizard.start_wizard(ctx)


_HANDLERS: dict[str, CallbackHandler] = {
    "home": _handle_home,
    "help": _handle_help,
    "status": _handle_status,
    "alerts": _handle_alerts_page,
    "alert": _handle_alert_card,
    "alert_enable": _handle_alert_enable,
    "alert_disable": _handle_alert_disable,
    "alert_cd": _handle_alert_cooldown,
    "alert_cd_set": _handle_alert_cooldown_set,
    "alert_cd_custom": _handle_alert_cooldown_custom,
    "alert_del": _handle_alert_delete,
    "alert_del_yes": _handle_alert_delete_yes,
    "mute_menu": _handle_mute_menu,
    "mute_set": _handle_mute_set,
    "unmute": _handle_unmute,
    "new": _handle_new,
    "noop": _handle_noop,
}

_WIZARD_ACTIONS = frozenset(
    {
        "wz_rule",
        "wz_sens",
        "wz_filters_custom",
        "wz_filters_skip",
        "wz_cd",
        "wz_cd_custom",
        "wz_confirm",
        "wz_back",
        "wz_cancel",
    }
)


async def dispatch_callback(
    ctx: CommandContext,
    action: str,
    args: list[str],
) -> CallbackResult:
    """Route a parsed callback_data tuple to its handler.

    Unknown actions produce a non-destructive toast — typically the
    result of an upgrade that invalidated older keyboards; the user
    can press ``home`` to refresh.
    """

    if action in _WIZARD_ACTIONS:
        return await wizard.handle_wizard_callback(action, ctx, args)
    handler = _HANDLERS.get(action)
    if handler is None:
        return CallbackResult(toast="Кнопка устарела, откройте /start")
    return await handler(ctx, args)


async def handle_pending_text_input(
    ctx: CommandContext,
    text: str,
) -> CallbackResult | None:
    """Resolve a text message against an active ``pending_input`` slot.

    The dispatcher consults this helper before treating a non-slash
    message as noise. It returns ``None`` when there is no active
    session expecting text, so the existing "ignore free-form
    messages" behaviour is preserved when the UI is idle.
    """

    pending = _ui.get_pending_input(ctx)
    if pending is None:
        # No explicit slot — defer to the wizard only if a wizard
        # session is actually running; otherwise the dispatcher
        # treats the message as noise.
        return await wizard.handle_wizard_text(ctx, text)

    kind = pending.get("kind")
    if kind == "alert_cooldown":
        token = pending.get("token")
        if not isinstance(token, str):
            return CallbackResult(toast="Сессия устарела")
        return await _apply_cooldown(ctx, token=token, raw_value=text.strip())

    return await wizard.handle_wizard_text(ctx, text)
