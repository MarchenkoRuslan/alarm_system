"""Service/utility command handlers.

Groups all non-alert user-facing commands:

- session lifecycle: ``/start``, ``/stop``
- discoverability: ``/help``, ``/status``
- wizard entry: ``/new``
- mute controls: ``/mute``, ``/unmute``
- side-channel views: ``/bindings``, ``/history``

These all share the same minimal shape (accept a ``CommandContext``,
return text) and none of them touch alert rows directly, so keeping
them in one module makes the read/write/alert handlers in
``alerts.py`` stand out cleanly as the "business" surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.routes.telegram_commands import _keyboards
from alarm_system.api.routes.telegram_commands._args import (
    format_duration_seconds,
    parse_duration_seconds,
    parse_int,
)
from alarm_system.api.routes.telegram_commands._context import (
    BackendError,
    CommandContext,
    CommandResult,
)
from alarm_system.entities import ChannelBinding, DeliveryChannel


# --------------------------------------------------------------------------
# Session lifecycle + discoverability: /start, /stop, /help, /status, /new.
# --------------------------------------------------------------------------


def _tg_binding_id(user_id: str, chat_id: str) -> str:
    """Deterministic binding id for the Telegram channel.

    Single source of truth used by ``/start`` and ``/stop`` so the pair
    stays in lockstep even if the format changes.
    """

    return f"tg-{user_id}-{chat_id}"


async def handle_start(ctx: CommandContext) -> CommandResult:
    binding = ChannelBinding.model_validate(
        {
            "binding_id": _tg_binding_id(ctx.user_id, ctx.chat_id),
            "user_id": ctx.user_id,
            "channel": DeliveryChannel.TELEGRAM,
            "destination": ctx.chat_id,
            "is_verified": True,
        }
    )
    try:
        ctx.store.upsert_binding(binding)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    ctx.set_reply_markup(_keyboards.home_menu())
    return (
        "Привет. Я подключен и могу отправлять алерты.\n"
        "Используйте кнопки ниже или команды /alerts, /new, /status."
    )


async def handle_stop(ctx: CommandContext) -> CommandResult:
    binding_id = _tg_binding_id(ctx.user_id, ctx.chat_id)
    try:
        deleted = ctx.store.delete_binding(binding_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if not deleted:
        return (
            "В этом чате нет активной привязки. "
            "Используйте /start, чтобы подключиться."
        )
    return (
        "Этот чат отвязан. Я больше не буду присылать сюда алерты.\n"
        "Чтобы подключиться снова, отправьте /start."
    )


async def handle_help(_: CommandContext) -> CommandResult:
    # Imported lazily to avoid a circular dependency on the registry.
    from alarm_system.api.routes.telegram_commands._registry import (
        build_help_text,
    )

    return build_help_text()


async def handle_status(ctx: CommandContext) -> CommandResult:
    try:
        active_alerts = ctx.store.list_alerts(
            user_id=ctx.user_id,
            include_disabled=False,
        )
        all_alerts = ctx.store.list_alerts(
            user_id=ctx.user_id,
            include_disabled=True,
        )
        bindings = ctx.store.list_bindings(user_id=ctx.user_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    mute_until = ctx.mute_store.get_mute_until(ctx.user_id)

    lines = ["Ваш статус:"]
    lines.append(
        f"- алертов активно: {len(active_alerts)} из {len(all_alerts)}"
    )
    lines.append(f"- привязанных каналов: {len(bindings)}")
    if mute_until is None:
        lines.append("- тишина: выключена")
    else:
        remaining = max(
            0,
            int((mute_until - datetime.now(timezone.utc)).total_seconds()),
        )
        lines.append(
            "- тишина: активна, осталось "
            f"{format_duration_seconds(remaining)} "
            f"(до {mute_until.isoformat(timespec='seconds')})"
        )
    return "\n".join(lines)


async def handle_new(ctx: CommandContext) -> CommandResult:
    """Entry point of the interactive create-alert wizard.

    The wizard's public surface is a :class:`CallbackResult`; for the
    slash-command entry we unpack it back into ``(text, reply_markup)``
    shape that the command dispatcher expects. The import is lazy to
    keep the command registry -> service -> wizard -> alerts chain
    import-order-safe.
    """

    from alarm_system.api.routes.telegram_commands.wizard import start_wizard

    result = await start_wizard(ctx)
    ctx.set_reply_markup(result.reply_markup)
    return result.text or "Мастер создания алерта недоступен."


# --------------------------------------------------------------------------
# Mute controls: /mute, /unmute.
# --------------------------------------------------------------------------


_MAX_MUTE_SECONDS = 30 * 24 * 3600


async def handle_mute(ctx: CommandContext) -> CommandResult:
    duration_arg = ctx.args.first_positional()
    if duration_arg is None:
        return (
            "Используйте: /mute <duration>. Примеры: 30m, 2h, 1d. "
            "Максимум — 30d."
        )
    try:
        seconds = parse_duration_seconds(duration_arg)
    except ValueError as exc:
        return f"Некорректный интервал: {exc}"
    if seconds > _MAX_MUTE_SECONDS:
        return "Максимальная длительность тишины — 30d."
    try:
        active_until = ctx.mute_store.set_mute(
            user_id=ctx.user_id,
            seconds=seconds,
        )
    except ValueError as exc:
        return f"Некорректный интервал: {exc}"
    return (
        f"Тишина включена на {format_duration_seconds(seconds)}. "
        f"До {active_until.isoformat(timespec='seconds')}."
    )


async def handle_unmute(ctx: CommandContext) -> CommandResult:
    cleared = ctx.mute_store.clear_mute(ctx.user_id)
    if cleared:
        return "Тишина снята. Буду снова присылать алерты."
    return "Тишина и так не была включена."


# --------------------------------------------------------------------------
# Side-channel views: /bindings, /history.
# --------------------------------------------------------------------------


async def handle_bindings(ctx: CommandContext) -> CommandResult:
    try:
        bindings = ctx.store.list_bindings(user_id=ctx.user_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if not bindings:
        return (
            "У вас нет привязанных каналов. "
            "Отправьте /start в нужном чате, чтобы подключить Telegram."
        )
    lines = ["Ваши каналы доставки:"]
    for binding in bindings:
        verified = "verified" if binding.is_verified else "unverified"
        lines.append(
            f"- {binding.binding_id}: "
            f"{binding.channel.value} -> {binding.destination} ({verified})"
        )
    return "\n".join(lines)


_DEFAULT_HISTORY_LIMIT = 10
_MAX_HISTORY_LIMIT = 50


async def handle_history(ctx: CommandContext) -> CommandResult:
    limit = _DEFAULT_HISTORY_LIMIT
    raw_limit = ctx.args.first_positional()
    if raw_limit is not None:
        try:
            limit = parse_int(raw_limit)
        except ValueError:
            return f"Некорректное N: {raw_limit!r}"
        if limit <= 0:
            return "N должно быть положительным."
        if limit > _MAX_HISTORY_LIMIT:
            return f"N слишком большое, максимум {_MAX_HISTORY_LIMIT}."
    attempts = ctx.attempt_store.list_by_user(
        user_id=ctx.user_id,
        limit=limit,
    )
    if not attempts:
        return "История доставок пуста."
    lines = [f"Последние доставки (до {limit}):"]
    for attempt in attempts:
        enqueued = attempt.enqueued_at.isoformat(timespec="seconds")
        line = (
            f"- {enqueued} [{attempt.status.value}] "
            f"{attempt.channel.value} -> {attempt.alert_id} "
            f"(attempt {attempt.attempt_no})"
        )
        if attempt.error_code:
            line += f" error={attempt.error_code}"
        lines.append(line)
    return "\n".join(lines)
