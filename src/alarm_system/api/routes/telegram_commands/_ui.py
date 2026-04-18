"""Shared UI primitives for the interactive Telegram bot layer.

This module is the single source of truth for constants, data types,
session manipulation helpers, and pure text rendering that are reused
by the callback dispatcher, the create-alert wizard, and the read-only
``/alerts`` / ``/alert`` slash commands.

Keeping everything here avoids three earlier problems:

- two copies of ``_alert_card_text`` / ``_store_alert_tokens`` drifting
  apart between ``_callbacks.py`` and ``alerts_read.py``;
- ``wizard.py`` reaching up into ``_callbacks.py`` for ``SESSION_TTL``
  and ``pending_input`` keys;
- callback handlers repeating the lookup-then-fetch-then-upsert
  pattern for every alert-card action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreConflictError,
    AlertStoreContractError,
)
from alarm_system.api.routes.telegram_commands import _keyboards
from alarm_system.api.routes.telegram_commands._context import (
    AlertNotFoundError,
    BackendError,
    CommandContext,
)
from alarm_system.entities import Alert


# Session lifetime for every transient wizard/list/cooldown slot. Ten
# minutes is long enough for a thoughtful user to finish the wizard
# and short enough that stale drafts never leak indefinitely.
SESSION_TTL_SECONDS = 10 * 60

# Top-level keys inside the per-user session payload.
WIZARD_KEY = "wizard"
ALERTS_INDEX_KEY = "alerts_index"
PENDING_INPUT_KEY = "pending_input"


# --------------------------------------------------------------------------
# CallbackResult — output contract shared by callback and wizard handlers.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CallbackResult:
    """Outcome of a callback-button press.

    - ``text`` + ``reply_markup``: the dispatcher edits the original
      message in place so the UI feels like a native stateful screen.
    - ``toast``: short feedback shown as the temporary "answer" popup
      of a callback query (used for errors, cancellations, no-ops).
    - ``show_alert``: escalates ``toast`` from a transient notification
      to a modal dialog the user must dismiss.
    """

    text: str | None = None
    reply_markup: dict[str, Any] | None = None
    toast: str | None = None
    show_alert: bool = False


# --------------------------------------------------------------------------
# Session manipulation (single write = load-merge-save to keep TTL fresh).
# --------------------------------------------------------------------------


def load_session(ctx: CommandContext) -> dict[str, Any]:
    """Return a mutable copy of the current session or an empty dict."""

    return ctx.session_store.load(ctx.user_id) or {}


def save_session(ctx: CommandContext, payload: dict[str, Any]) -> None:
    ctx.session_store.save(
        user_id=ctx.user_id,
        payload=payload,
        ttl_seconds=SESSION_TTL_SECONDS,
    )


def clear_session(ctx: CommandContext) -> None:
    ctx.session_store.clear(ctx.user_id)


def set_session_value(
    ctx: CommandContext,
    key: str,
    value: Any,
) -> None:
    session = load_session(ctx)
    session[key] = value
    save_session(ctx, session)


def pop_session_value(ctx: CommandContext, key: str) -> None:
    session = load_session(ctx)
    if session.pop(key, None) is not None:
        save_session(ctx, session)


# --- Specialised slots the UI cares about ---------------------------------


def store_alert_tokens(ctx: CommandContext, alert_ids: list[str]) -> None:
    """Persist the short-token -> alert_id mapping for the next page.

    Tokens are zero-padded indices (``"00"``, ``"01"``, ...) so every
    ``callback_data`` stays well under the Bot API 64-byte ceiling no
    matter how long the real ``alert_id`` is.
    """

    session = load_session(ctx)
    session[ALERTS_INDEX_KEY] = {
        f"{idx:02d}": alert_id for idx, alert_id in enumerate(alert_ids)
    }
    save_session(ctx, session)


def lookup_alert_id(ctx: CommandContext, token: str) -> str | None:
    session = ctx.session_store.load(ctx.user_id) or {}
    index = session.get(ALERTS_INDEX_KEY) or {}
    alert_id = index.get(token)
    return alert_id if isinstance(alert_id, str) else None


def set_pending_input(
    ctx: CommandContext,
    *,
    kind: str,
    **extra: Any,
) -> None:
    payload = {"kind": kind, **extra}
    set_session_value(ctx, PENDING_INPUT_KEY, payload)


def get_pending_input(ctx: CommandContext) -> dict[str, Any] | None:
    session = ctx.session_store.load(ctx.user_id)
    if not session:
        return None
    pending = session.get(PENDING_INPUT_KEY)
    return pending if isinstance(pending, dict) else None


def clear_pending_input(ctx: CommandContext) -> None:
    pop_session_value(ctx, PENDING_INPUT_KEY)


# --------------------------------------------------------------------------
# Pure rendering — shared text builders.
# --------------------------------------------------------------------------


def render_alert_card(alert: Alert) -> str:
    """Human-readable view of a single alert for the inline card."""

    status = "включен" if alert.enabled else "выключен"
    channels = ", ".join(c.value for c in alert.channels) or "-"
    lines = [
        f"Алерт {alert.alert_id}",
        f"Тип: {alert.alert_type.value}",
        f"Статус: {status}",
        f"Cooldown: {alert.cooldown_seconds}s",
        f"Каналы: {channels}",
        f"Правило: {alert.rule_id}#{alert.rule_version}",
        f"Версия записи: {alert.version}",
        f"Создан: {alert.created_at.isoformat(timespec='seconds')}",
    ]
    if alert.filters_json:
        lines.append(f"Фильтры: {alert.filters_json}")
    return "\n".join(lines)


def alert_card_view(*, token: str, alert: Alert, toast: str | None = None) -> CallbackResult:
    """Assemble a complete card response (text + inline keyboard)."""

    return CallbackResult(
        text=render_alert_card(alert),
        reply_markup=_keyboards.alert_card(token=token, enabled=alert.enabled),
        toast=toast,
    )


# --------------------------------------------------------------------------
# Alert-card guards: turn the common lookup-then-fetch pattern into
# a single call with a unified failure toast, so every handler stops
# re-implementing the same boilerplate.
# --------------------------------------------------------------------------


_STALE_LIST_TOAST = "Список устарел, откройте заново"
_UNKNOWN_BUTTON_TOAST = "Кнопка устарела"
_NOT_FOUND_TOAST = "Алерт не найден"


def require_first_arg(args: list[str]) -> tuple[str | None, CallbackResult | None]:
    """Return ``(first_arg, None)`` or ``(None, error_result)``.

    The callback-data parser splits the payload on ``:``, so the first
    element is always the "primary" argument — an alert token for
    card actions, a duration string for mute presets, a scenario id
    for the wizard, and so on. Centralising the "missing primary
    arg" toast keeps every handler a one-liner at the top.
    """

    if not args:
        return None, CallbackResult(toast=_UNKNOWN_BUTTON_TOAST)
    return args[0], None


def resolve_card_alert(
    ctx: CommandContext,
    token: str,
) -> Alert | CallbackResult:
    """Resolve a card token back to an owned ``Alert`` or an error toast.

    Centralises the two-step ``lookup_alert_id -> fetch_owned_alert``
    chain used by every alert-card action. Backend outages are
    intentionally left to propagate as :class:`BackendError` so the
    webhook dispatcher maps them to a 503 like everything else.
    """

    alert_id = lookup_alert_id(ctx, token)
    if alert_id is None:
        return CallbackResult(toast=_STALE_LIST_TOAST)
    try:
        return ctx.fetch_owned_alert(alert_id)
    except AlertNotFoundError:
        return CallbackResult(toast=_NOT_FOUND_TOAST)


def persist_alert_update(
    ctx: CommandContext,
    *,
    alert: Alert,
    updates: dict[str, Any],
) -> Alert | CallbackResult:
    """Upsert an ``Alert`` with optimistic concurrency, mapping failures.

    The return contract is symmetric with :func:`resolve_card_alert`
    so callers can chain ``if isinstance(x, CallbackResult): return x``
    without bespoke error handling at every site.
    """

    try:
        return ctx.store.upsert_alert(
            alert.model_copy(update=updates),
            expected_version=alert.version,
        )
    except AlertStoreConflictError:
        return CallbackResult(
            toast="Алерт изменен параллельно, попробуйте снова",
        )
    except AlertStoreContractError as exc:
        return CallbackResult(toast=f"Не удалось обновить: {exc}")
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc


# --------------------------------------------------------------------------
# Shared cooldown parsing used by both the card and the wizard.
# --------------------------------------------------------------------------


MAX_COOLDOWN_SECONDS = 24 * 3600


def parse_cooldown_value(raw_value: str) -> int | CallbackResult:
    """Strict integer parse with the MVP 0..86400 domain constraint."""

    from alarm_system.api.routes.telegram_commands._args import parse_int

    try:
        value = parse_int(raw_value)
    except ValueError:
        return CallbackResult(toast=f"Некорректное значение: {raw_value}")
    if value < 0 or value > MAX_COOLDOWN_SECONDS:
        return CallbackResult(
            toast=f"Cooldown должен быть в диапазоне 0..{MAX_COOLDOWN_SECONDS}"
        )
    return value
