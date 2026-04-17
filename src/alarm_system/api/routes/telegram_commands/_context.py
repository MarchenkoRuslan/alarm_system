"""Shared command execution context."""

from __future__ import annotations

from dataclasses import dataclass

from alarm_system.alert_store import AlertStore, AlertStoreBackendError
from alarm_system.api.routes.telegram_commands._args import CommandArgs
from alarm_system.api.routes.telegram_commands._errors import (
    AlertNotFoundError,
    BackendError,
)
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.entities import Alert
from alarm_system.state import DeliveryAttemptStore, MuteStore


@dataclass(frozen=True)
class CommandContext:
    """Per-invocation state shared across command handlers.

    Handlers keep their signatures small (``-> CommandResult``) and
    delegate common queries (ownership checks, backend error mapping)
    to the helper methods on this class so the dispatcher can catch a
    uniform exception set.
    """

    store: AlertStore
    telegram_client: TelegramApiClient
    mute_store: MuteStore
    attempt_store: DeliveryAttemptStore
    user_id: str
    chat_id: str
    args: CommandArgs
    rule_identities: frozenset[tuple[str, int]] | None = None

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


CommandResult = str
