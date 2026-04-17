"""Domain errors shared by Telegram command handlers.

Using dedicated exception types keeps handler signatures simple (``->
CommandResult``) and removes tagged-union returns like ``Alert | str``.
The dispatcher is responsible for turning these into user-facing text or
HTTP status codes.
"""

from __future__ import annotations


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
