"""Read-only alert commands: /alerts, /alert, /templates.

The slash commands are now entry points to the interactive card/list
UI: ``/alerts`` renders a paginated keyboard and ``/alert <id>``
renders a card with inline action buttons (enable/disable, cooldown,
delete). The legacy plain-text output is preserved for the ``--all``
flag where power users want to see disabled alerts at a glance.

Session token indexing, card rendering, and TTL handling all live in
:mod:`_ui` so this module is pure orchestration on top.
"""

from __future__ import annotations

from alarm_system.alert_store import AlertStoreBackendError
from alarm_system.api.alert_presets import ALERT_CREATE_EXAMPLES
from alarm_system.api.routes.telegram_commands import _keyboards, _ui
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands._errors import BackendError


_LEGACY_LIST_LIMIT = 20


def _legacy_list_text(alerts: list) -> str:
    lines = ["Все ваши алерты:"]
    for alert in alerts[:_LEGACY_LIST_LIMIT]:
        status = "on" if alert.enabled else "off"
        lines.append(
            f"- {alert.alert_id}: {alert.alert_type.value}, "
            f"cooldown={alert.cooldown_seconds}s, {status}"
        )
    if len(alerts) > _LEGACY_LIST_LIMIT:
        lines.append(f"... и еще {len(alerts) - _LEGACY_LIST_LIMIT}")
    return "\n".join(lines)


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
        ctx.set_reply_markup(_keyboards.empty_alerts_menu())
        return (
            "Алертов пока нет. Нажмите 'Создать алерт', "
            "чтобы запустить мастер."
            if include_disabled
            else "Активных алертов пока нет. "
            "Нажмите 'Создать алерт' или посмотрите /templates."
        )

    if include_disabled:
        # Power-user path: plain text listing of every alert, ids
        # included; no inline keyboard so the output stays scriptable.
        return _legacy_list_text(alerts)

    # Default: interactive paginated keyboard on first page.
    page_size = _keyboards.ALERTS_PAGE_SIZE
    visible = alerts[:page_size]
    _ui.store_alert_tokens(ctx, [a.alert_id for a in visible])
    total_pages = (len(alerts) + page_size - 1) // page_size if alerts else 1
    items: list[tuple[str, str]] = []
    for idx, alert in enumerate(visible):
        status = "вкл" if alert.enabled else "выкл"
        label = (
            f"{alert.alert_type.value} · {status} · "
            f"cd {alert.cooldown_seconds}s"
        )
        items.append((f"{idx:02d}", label))
    ctx.set_reply_markup(
        _keyboards.alerts_list(
            page=0,
            total_pages=total_pages,
            items=items,
        )
    )
    return (
        f"Ваши активные алерты (страница 1 из {max(total_pages, 1)}, "
        f"всего {len(alerts)}). Нажмите карточку для действий."
    )


async def handle_alert(ctx: CommandContext) -> CommandResult:
    alert_id = ctx.args.first_positional()
    if not alert_id:
        return "Используйте: /alert <alert_id>"
    alert = ctx.fetch_owned_alert(alert_id)
    # Register a single-entry token index so the card's inline buttons
    # (enable/disable/cooldown/delete) resolve back to this alert_id.
    _ui.store_alert_tokens(ctx, [alert.alert_id])
    ctx.set_reply_markup(
        _keyboards.alert_card(token="00", enabled=alert.enabled)
    )
    return _ui.render_alert_card(alert)


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
        "\nПример: /create trader_positions cooldown=120"
    )
    lines.append(
        "Совет: удобнее создавать кнопкой 'Создать алерт' в /start."
    )
    return "\n".join(lines)
