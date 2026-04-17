"""Mute commands: /mute, /unmute."""

from __future__ import annotations

from alarm_system.api.routes.telegram_commands._args import (
    format_duration_seconds,
    parse_duration_seconds,
)
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)


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
    max_seconds = 30 * 24 * 3600
    if seconds > max_seconds:
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
