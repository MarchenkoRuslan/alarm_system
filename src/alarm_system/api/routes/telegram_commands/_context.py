"""Shared command execution context and domain errors.

The :class:`CommandContext` carries everything handlers need to touch
state (stores, telegram client, session, args). The exception types
defined below let handlers keep their signatures small
(``-> CommandResult``) instead of returning tagged unions — the
dispatcher is responsible for turning them into user-facing text or
HTTP status codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alarm_system.alert_store import AlertStore, AlertStoreBackendError
from alarm_system.api.routes.telegram_commands._args import CommandArgs
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.entities import Alert
from alarm_system.state import (
    DeliveryAttemptStore,
    InMemorySessionStore,
    MuteStore,
    SessionStore,
)


class BackendError(RuntimeError):
    """Raised when an upstream store is unavailable (surfaces as HTTP 503)."""


class AlertNotFoundError(RuntimeError):
    """Raised when a requested alert does not exist or is not owned."""

    def __init__(self, alert_id: str) -> None:
        super().__init__(f"alert {alert_id} not found")
        self.alert_id = alert_id


class RuleIdentityNotAllowedError(RuntimeError):
    """Raised when an alert references a rule not in the whitelist."""

    def __init__(self, rule_id: str, rule_version: int) -> None:
        super().__init__(
            f"rule identity {rule_id}#{rule_version} is not registered"
        )
        self.rule_id = rule_id
        self.rule_version = rule_version


@dataclass
class CommandContext:
    """Per-invocation state shared across command/callback handlers.

    Handlers keep their signatures small (``-> CommandResult``) and
    delegate common queries (ownership checks, backend error mapping)
    to the helper methods on this class so the dispatcher can catch a
    uniform exception set.

    ``reply_markup`` is a write slot for slash-command handlers that
    want to attach an inline keyboard to their response; the webhook
    dispatcher forwards it to ``sendMessage``. Callback handlers
    return a :class:`CallbackResult` directly and do not use this
    slot.
    """

    store: AlertStore
    telegram_client: TelegramApiClient
    mute_store: MuteStore
    attempt_store: DeliveryAttemptStore
    user_id: str
    chat_id: str
    args: CommandArgs
    session_store: SessionStore = field(default_factory=InMemorySessionStore)
    reply_markup: dict[str, Any] | None = None

    def fetch_owned_alert(self, alert_id: str) -> Alert:
        """Return an alert owned by the current user.

        Raises :class:`AlertNotFoundError` when the alert does not
        exist or belongs to another user (same response text avoids
        leaking cross-user existence). Backend outages surface as
        :class:`BackendError` so the dispatcher maps them to HTTP 503.
        """

        try:
            alert = self.store.get_alert(alert_id)
        except AlertStoreBackendError as exc:
            raise BackendError(str(exc)) from exc
        if alert is None or alert.user_id != self.user_id:
            raise AlertNotFoundError(alert_id)
        return alert

    def set_reply_markup(self, markup: dict[str, Any] | None) -> None:
        """Attach (or clear) an inline keyboard on the outgoing reply."""

        self.reply_markup = markup


CommandResult = str
