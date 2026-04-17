"""/history command handler."""

from __future__ import annotations

from alarm_system.api.routes.telegram_commands._args import parse_int
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)


_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


async def handle_history(ctx: CommandContext) -> CommandResult:
    limit = _DEFAULT_LIMIT
    raw_limit = ctx.args.first_positional()
    if raw_limit is not None:
        try:
            limit = parse_int(raw_limit)
        except ValueError:
            return f"Некорректное N: {raw_limit!r}"
        if limit <= 0:
            return "N должно быть положительным."
        if limit > _MAX_LIMIT:
            return f"N слишком большое, максимум {_MAX_LIMIT}."
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
