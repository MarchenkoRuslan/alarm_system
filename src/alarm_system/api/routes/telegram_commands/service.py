"""/start, /stop, /help, /status command handlers."""

from __future__ import annotations

from datetime import datetime, timezone

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.routes.telegram_commands._args import (
    format_duration_seconds,
)
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands._errors import BackendError
from alarm_system.entities import ChannelBinding, DeliveryChannel


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
    return (
        "Привет. Я подключен и могу отправлять алерты.\n"
        "Команды: /help, /alerts, /status"
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
