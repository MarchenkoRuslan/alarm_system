"""Read-only alert commands: /alerts, /alert, /templates."""

from __future__ import annotations

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands._errors import BackendError
from alarm_system.api.schemas import ALERT_CREATE_EXAMPLES


_ALERTS_DISPLAY_LIMIT = 20


async def handle_alerts(ctx: CommandContext) -> CommandResult:
    include_disabled = ctx.args.has_flag("all")
    try:
        alerts = ctx.store.list_alerts(
            user_id=ctx.user_id,
            include_disabled=include_disabled,
        )
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if not alerts:
        return (
            "Алертов пока нет." if include_disabled
            else "Активных алертов пока нет. "
            "Добавьте: /create <template_id> или посмотрите /templates."
        )
    title = "Все ваши алерты:" if include_disabled else "Ваши активные алерты:"
    lines = [title]
    for alert in alerts[:_ALERTS_DISPLAY_LIMIT]:
        status = "on" if alert.enabled else "off"
        lines.append(
            f"- {alert.alert_id}: {alert.alert_type.value}, "
            f"cooldown={alert.cooldown_seconds}s, {status}"
        )
    if len(alerts) > _ALERTS_DISPLAY_LIMIT:
        lines.append(f"... и еще {len(alerts) - _ALERTS_DISPLAY_LIMIT}")
    return "\n".join(lines)


async def handle_alert(ctx: CommandContext) -> CommandResult:
    alert_id = ctx.args.first_positional()
    if not alert_id:
        return "Используйте: /alert <alert_id>"
    alert = ctx.fetch_owned_alert(alert_id)
    channels = ", ".join(channel.value for channel in alert.channels)
    lines = [
        f"Алерт {alert.alert_id}:",
        f"- тип: {alert.alert_type.value}",
        f"- правило: {alert.rule_id}#{alert.rule_version}",
        f"- cooldown: {alert.cooldown_seconds}s",
        f"- каналы: {channels or '-'}",
        f"- enabled: {alert.enabled}",
        f"- версия: {alert.version}",
        f"- создан: {alert.created_at.isoformat(timespec='seconds')}",
    ]
    if alert.filters_json:
        lines.append(f"- filters: {alert.filters_json}")
    return "\n".join(lines)


async def handle_templates(_: CommandContext) -> CommandResult:
    lines = ["Доступные шаблоны для /create <template_id>:"]
    for template_id, body in ALERT_CREATE_EXAMPLES.items():
        summary = body.get("summary", "")
        value = body.get("value", {})
        alert_type = value.get("alert_type", "")
        cooldown = value.get("cooldown_seconds", "")
        lines.append(
            f"- {template_id}: {summary} "
            f"[type={alert_type}, cooldown={cooldown}s]"
        )
    lines.append(
        "\nПример: /create user_a_trader_position_updates cooldown=120"
    )
    return "\n".join(lines)
