"""Channel-binding commands: /bindings."""

from __future__ import annotations

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands._errors import BackendError


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
