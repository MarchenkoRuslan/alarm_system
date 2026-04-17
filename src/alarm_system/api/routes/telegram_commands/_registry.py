"""Command registry + metadata for Bot API ``setMyCommands``.

``COMMAND_CATALOG`` is the single source of truth for both the Telegram
client menu (via ``setMyCommands``) and the in-bot ``/help`` text. Each
entry carries a short ``description`` (<=256 chars for Bot API) and a
``long_description`` shown only inside ``/help``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands.alerts_read import (
    handle_alert,
    handle_alerts,
    handle_templates,
)
from alarm_system.api.routes.telegram_commands.alerts_write import (
    handle_create,
    handle_create_raw,
    handle_delete,
    handle_disable,
    handle_enable,
    handle_set_cooldown,
)
from alarm_system.api.routes.telegram_commands.bindings import handle_bindings
from alarm_system.api.routes.telegram_commands.history import handle_history
from alarm_system.api.routes.telegram_commands.mute import (
    handle_mute,
    handle_unmute,
)
from alarm_system.api.routes.telegram_commands.service import (
    handle_help,
    handle_start,
    handle_status,
    handle_stop,
)


CommandHandler = Callable[[CommandContext], Awaitable[CommandResult]]


@dataclass(frozen=True)
class CommandSpec:
    """Single source of truth for one slash command.

    The same spec feeds three consumers:

    - ``TELEGRAM_BOT_COMMANDS`` for the Bot API ``setMyCommands`` menu
      (uses ``command`` + ``description``);
    - ``build_command_registry`` for the webhook dispatcher (uses
      ``command`` + ``handler``);
    - ``build_help_text`` for the in-bot ``/help`` output (uses
      ``long_description`` + ``section``).
    """

    command: str
    handler: CommandHandler
    description: str
    long_description: str
    section: str


COMMAND_CATALOG: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="start",
        handler=handle_start,
        description="Привязать чат к аккаунту",
        long_description="/start — привязать этот чат к вашему аккаунту",
        section="Служебные",
    ),
    CommandSpec(
        command="stop",
        handler=handle_stop,
        description="Отвязать этот чат",
        long_description="/stop — отвязать этот чат",
        section="Служебные",
    ),
    CommandSpec(
        command="help",
        handler=handle_help,
        description="Справка по командам",
        long_description="/help — эта справка",
        section="Служебные",
    ),
    CommandSpec(
        command="status",
        handler=handle_status,
        description="Сводный статус",
        long_description="/status — сводный статус (алерты, mute, bindings)",
        section="Служебные",
    ),
    CommandSpec(
        command="alerts",
        handler=handle_alerts,
        description="Список активных алертов",
        long_description=(
            "/alerts [--all] — список алертов (по умолчанию только активные)"
        ),
        section="Чтение",
    ),
    CommandSpec(
        command="alert",
        handler=handle_alert,
        description="Детали алерта: /alert <id>",
        long_description="/alert <alert_id> — детали конкретного алерта",
        section="Чтение",
    ),
    CommandSpec(
        command="bindings",
        handler=handle_bindings,
        description="Привязанные каналы",
        long_description="/bindings — каналы доставки",
        section="Чтение",
    ),
    CommandSpec(
        command="history",
        handler=handle_history,
        description="Последние доставки",
        long_description=(
            "/history [N] — последние N доставок (по умолчанию 10, макс 50)"
        ),
        section="Чтение",
    ),
    CommandSpec(
        command="templates",
        handler=handle_templates,
        description="Шаблоны для /create",
        long_description="/templates — доступные шаблоны для /create",
        section="Чтение",
    ),
    CommandSpec(
        command="enable",
        handler=handle_enable,
        description="Включить алерт: /enable <id>",
        long_description="/enable <alert_id> — включить алерт",
        section="Управление",
    ),
    CommandSpec(
        command="disable",
        handler=handle_disable,
        description="Выключить алерт: /disable <id>",
        long_description="/disable <alert_id> — выключить алерт",
        section="Управление",
    ),
    CommandSpec(
        command="set_cooldown",
        handler=handle_set_cooldown,
        description="Сменить cooldown: /set_cooldown <id> <sec>",
        long_description=(
            "/set_cooldown <alert_id> <seconds> — сменить cooldown"
        ),
        section="Управление",
    ),
    CommandSpec(
        command="delete",
        handler=handle_delete,
        description="Удалить алерт: /delete <id> yes",
        long_description=(
            "/delete <alert_id> [yes] — удалить "
            "(повторите с 'yes' для подтверждения)"
        ),
        section="Управление",
    ),
    CommandSpec(
        command="mute",
        handler=handle_mute,
        description="Заглушить: /mute 30m|2h|1d",
        long_description=(
            "/mute <duration> — заглушить все алерты (примеры: 30m, 2h, 1d)"
        ),
        section="Тишина",
    ),
    CommandSpec(
        command="unmute",
        handler=handle_unmute,
        description="Снять тишину",
        long_description="/unmute — снять тишину",
        section="Тишина",
    ),
    CommandSpec(
        command="create",
        handler=handle_create,
        description="Создать из шаблона",
        long_description=(
            "/create <template_id> [alert_id=...] [cooldown=...] "
            "[enabled=true|false]"
        ),
        section="Создание",
    ),
    CommandSpec(
        command="create_raw",
        handler=handle_create_raw,
        description="Создать из JSON",
        long_description="/create_raw <json> — вставить полный JSON алерта",
        section="Создание",
    ),
)


# ``setMyCommands`` payload — Telegram UI command menu.
TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": spec.command, "description": spec.description}
    for spec in COMMAND_CATALOG
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
    tuple.
    """

    return {f"/{spec.command}": spec.handler for spec in COMMAND_CATALOG}
