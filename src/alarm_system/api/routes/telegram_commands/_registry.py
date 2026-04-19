"""Command registry + metadata for Bot API ``setMyCommands``.

``COMMAND_CATALOG`` is the single source of truth for both the Telegram
client menu (via ``setMyCommands``) and the in-bot ``/help`` text. Each
entry carries a short ``description`` (<=256 chars for Bot API) and a
``long_description`` shown only inside ``/help``.

Entries marked ``hidden=True`` stay registered in the dispatcher and
appear in ``/help`` under a separate "Расширенные" section so power
users can still invoke them, but they are intentionally omitted from
the ``setMyCommands`` menu to keep the Telegram client UI focused on
the interactive flow (inline keyboards + wizard).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands.alerts import (
    handle_alert,
    handle_alerts,
    handle_create,
    handle_create_raw,
    handle_delete,
    handle_disable,
    handle_enable,
    handle_set_cooldown,
    handle_set_filters,
    handle_templates,
)
from alarm_system.api.routes.telegram_commands.service import (
    handle_bindings,
    handle_help,
    handle_history,
    handle_mute,
    handle_new,
    handle_start,
    handle_status,
    handle_stop,
    handle_unmute,
)


CommandHandler = Callable[[CommandContext], Awaitable[CommandResult]]


@dataclass(frozen=True)
class CommandSpec:
    """Single source of truth for one slash command.

    The same spec feeds three consumers:

    - ``TELEGRAM_BOT_COMMANDS`` for the Bot API ``setMyCommands`` menu
      (uses ``command`` + ``description``; skips entries with
      ``hidden=True``);
    - ``build_command_registry`` for the webhook dispatcher (uses
      ``command`` + ``handler`` regardless of ``hidden``);
    - ``build_help_text`` for the in-bot ``/help`` output (uses
      ``long_description`` + ``section``; hidden commands surface in
      the "Расширенные" block).
    """

    command: str
    handler: CommandHandler
    description: str
    long_description: str
    section: str
    hidden: bool = False


COMMAND_CATALOG: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="start",
        handler=handle_start,
        description="Главное меню бота",
        long_description="/start — главное меню (кнопки алертов, мастера)",
        section="Базовые",
    ),
    CommandSpec(
        command="alerts",
        handler=handle_alerts,
        description="Мои алерты",
        long_description=(
            "/alerts — интерактивный список ваших алертов. "
            "Флаг --all показывает выключенные тоже."
        ),
        section="Базовые",
    ),
    CommandSpec(
        command="new",
        handler=handle_new,
        description="Создать алерт мастером",
        long_description=(
            "/new — мастер: сначала что отслеживать (сценарий), "
            "затем теги рынков и пороги сигналов, в конце пауза между уведомлениями"
        ),
        section="Базовые",
    ),
    CommandSpec(
        command="status",
        handler=handle_status,
        description="Мой статус",
        long_description="/status — сводка по алертам, mute, каналам",
        section="Базовые",
    ),
    CommandSpec(
        command="mute",
        handler=handle_mute,
        description="Заглушить: /mute 30m",
        long_description=(
            "/mute <duration> — заглушить все алерты (примеры: 30m, 2h, 1d)"
        ),
        section="Базовые",
    ),
    CommandSpec(
        command="unmute",
        handler=handle_unmute,
        description="Снять тишину",
        long_description="/unmute — снять тишину",
        section="Базовые",
    ),
    CommandSpec(
        command="help",
        handler=handle_help,
        description="Справка",
        long_description="/help — список команд",
        section="Базовые",
    ),
    CommandSpec(
        command="stop",
        handler=handle_stop,
        description="Отвязать этот чат",
        long_description="/stop — отвязать этот чат",
        section="Базовые",
    ),
    # Hidden / advanced commands below: the interactive UI covers them,
    # but we keep them working for scripts and power users.
    CommandSpec(
        command="alert",
        handler=handle_alert,
        description="Детали алерта: /alert <id>",
        long_description="/alert <alert_id> — детали конкретного алерта",
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="bindings",
        handler=handle_bindings,
        description="Привязанные каналы",
        long_description="/bindings — каналы доставки",
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="history",
        handler=handle_history,
        description="Последние доставки",
        long_description=(
            "/history [N] — последние N доставок (по умолчанию 10, макс 50)"
        ),
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="templates",
        handler=handle_templates,
        description="Шаблоны для /create",
        long_description="/templates — доступные шаблоны для /create",
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="enable",
        handler=handle_enable,
        description="Включить алерт: /enable <id>",
        long_description="/enable <alert_id> — включить алерт",
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="disable",
        handler=handle_disable,
        description="Выключить алерт: /disable <id>",
        long_description="/disable <alert_id> — выключить алерт",
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="set_cooldown",
        handler=handle_set_cooldown,
        description="Сменить cooldown: /set_cooldown <id> <sec>",
        long_description=(
            "/set_cooldown <alert_id> <seconds> — сменить cooldown"
        ),
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="set_filters",
        handler=handle_set_filters,
        description="Фильтры алерта: /set_filters <id> k=v",
        long_description=(
            "/set_filters <alert_id> key=value ... — обновить пороги "
            "фильтра (дополнительно к правилам сервера)"
        ),
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="delete",
        handler=handle_delete,
        description="Удалить алерт: /delete <id> yes",
        long_description=(
            "/delete <alert_id> [yes] — удалить "
            "(повторите с 'yes' для подтверждения)"
        ),
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="create",
        handler=handle_create,
        description="Создать из шаблона",
        long_description=(
            "/create <template_id> [alert_id=...] [cooldown=...] "
            "[enabled=true|false] [return_1m_pct_min=...] ..."
        ),
        section="Расширенные",
        hidden=True,
    ),
    CommandSpec(
        command="create_raw",
        handler=handle_create_raw,
        description="Создать из JSON",
        long_description="/create_raw <json> — вставить полный JSON алерта",
        section="Расширенные",
        hidden=True,
    ),
)


# ``setMyCommands`` payload — only the visible commands end up in the
# Telegram client menu so the menu stays focused on the interactive
# flow. Hidden commands still dispatch normally and show up in /help.
TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": spec.command, "description": spec.description}
    for spec in COMMAND_CATALOG
    if not spec.hidden
]


def build_help_text() -> str:
    """Assemble ``/help`` text from the catalog, grouped by section."""

    lines: list[str] = ["Доступные команды:", ""]
    current_section: str | None = None
    for spec in COMMAND_CATALOG:
        if spec.section != current_section:
            if current_section is not None:
                lines.append("")
            lines.append(f"{spec.section}:")
            current_section = spec.section
        lines.append(f"  {spec.long_description}")
    return "\n".join(lines)


def build_command_registry() -> dict[str, CommandHandler]:
    """Map ``/command -> handler`` derived from :data:`COMMAND_CATALOG`.

    Using the catalog as the single source of truth means adding a new
    command cannot silently miss either the Bot API menu, ``/help``
    output, or the dispatcher — all three are projections of the same
    tuple. Hidden commands are fully dispatchable; only the Bot API
    menu skips them.
    """

    return {f"/{spec.command}": spec.handler for spec in COMMAND_CATALOG}
