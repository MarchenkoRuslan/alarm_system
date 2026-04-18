"""Telegram bot slash command handlers.

Each command module exposes a small async handler that accepts a
``CommandContext`` and returns the response text. Handlers do not send
messages themselves; the dispatcher in
``alarm_system.api.routes.telegram_webhook`` owns the single
``_send_message_or_502`` helper for delivery.

The public surface here is intentionally narrow: ``CommandContext``,
``CommandResult``, ``BackendError``, ``AlertNotFoundError``,
``RuleIdentityNotAllowedError``, ``TELEGRAM_BOT_COMMANDS``,
``build_command_registry`` and ``build_help_text`` are everything a
router needs.
"""

from alarm_system.api.routes.telegram_commands._args import (
    CommandArgs,
    parse_bool,
    parse_duration_seconds,
    parse_int,
    split_command,
)
from alarm_system.api.routes.telegram_commands._context import (
    AlertNotFoundError,
    BackendError,
    CommandContext,
    CommandResult,
    RuleIdentityNotAllowedError,
)
from alarm_system.api.routes.telegram_commands._registry import (
    TELEGRAM_BOT_COMMANDS,
    build_command_registry,
    build_help_text,
)

__all__ = [
    "AlertNotFoundError",
    "BackendError",
    "CommandArgs",
    "CommandContext",
    "CommandResult",
    "RuleIdentityNotAllowedError",
    "TELEGRAM_BOT_COMMANDS",
    "build_command_registry",
    "build_help_text",
    "parse_bool",
    "parse_duration_seconds",
    "parse_int",
    "split_command",
]
