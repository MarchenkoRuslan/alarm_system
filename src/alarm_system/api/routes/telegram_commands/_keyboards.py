"""Inline keyboard factories for the interactive Telegram bot UI.

All factories return a ``reply_markup`` dict ready for the Bot API
``sendMessage`` / ``editMessageText`` calls. The shape is the minimal
``InlineKeyboardMarkup`` object: ``{"inline_keyboard": [[button, ...]]}``.

``callback_data`` format is versioned for forward compatibility:

    ``v1:<action>:<arg1>[:<arg2>...]``

Bot API limits ``callback_data`` to 64 bytes, so any long payload
(full alert ids, filter strings, raw JSON) must be stored in the
``SessionStore`` and referenced by short tokens here. Actions are
kept under ~16 chars and arguments are expected to be short
identifiers (numeric indices, status flags, page numbers).
"""

from __future__ import annotations

from typing import Any


CALLBACK_VERSION = "v1"

# Page size for paginated alert lists. Telegram renders inline
# keyboards of up to ~100 buttons but readable pages are small.
ALERTS_PAGE_SIZE = 5


def _button(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def _cb(action: str, *args: str) -> str:
    """Build a versioned callback_data string.

    Raises ``ValueError`` when the resulting payload exceeds 64 bytes
    so we fail loudly during development rather than silently in
    Telegram (which would reject the button).
    """

    parts = [CALLBACK_VERSION, action, *args]
    data = ":".join(parts)
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback_data too long: {data!r}")
    return data


def parse_callback(data: str) -> tuple[str, list[str]] | None:
    """Parse ``v1:<action>:<args...>`` callback_data.

    Returns ``None`` for payloads that do not match the current
    version — callers should answer the callback with a short toast
    like "Кнопка устарела" and refresh the message.
    """

    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 2:
        return None
    if parts[0] != CALLBACK_VERSION:
        return None
    action = parts[1]
    args = parts[2:]
    return action, args


def home_menu() -> dict[str, Any]:
    """Top-level menu shown by ``/start`` and the home button."""

    return {
        "inline_keyboard": [
            [_button("Мои алерты", _cb("alerts", "0"))],
            [_button("Создать алерт", _cb("new"))],
            [_button("Статус", _cb("status"))],
            [_button("Тишина", _cb("mute_menu"))],
            [_button("Помощь", _cb("help"))],
        ]
    }


def back_home() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [_button("В меню", _cb("home"))],
        ]
    }


def empty_alerts_menu() -> dict[str, Any]:
    """Two-button keyboard shown when the user has no alerts yet."""

    return {
        "inline_keyboard": [
            [_button("Создать алерт", _cb("new"))],
            [_button("В меню", _cb("home"))],
        ]
    }


def alerts_list(
    *,
    page: int,
    total_pages: int,
    items: list[tuple[str, str]],
) -> dict[str, Any]:
    """List page of alerts.

    ``items`` is a list of ``(short_token, label)`` tuples. Tokens are
    short stable identifiers (1-2 chars) assigned by the caller and
    resolved back to ``alert_id`` via the session store; this keeps
    ``callback_data`` safely under 64 bytes regardless of the real
    alert id length.
    """

    rows: list[list[dict[str, str]]] = []
    for token, label in items:
        rows.append([_button(label, _cb("alert", token))])

    nav: list[dict[str, str]] = []
    if page > 0:
        nav.append(_button("←", _cb("alerts", str(page - 1))))
    nav.append(_button(f"{page + 1}/{max(total_pages, 1)}", _cb("noop")))
    if page + 1 < total_pages:
        nav.append(_button("→", _cb("alerts", str(page + 1))))
    if nav:
        rows.append(nav)

    rows.append([_button("Создать алерт", _cb("new"))])
    rows.append([_button("В меню", _cb("home"))])
    return {"inline_keyboard": rows}


def alert_card(
    *,
    token: str,
    enabled: bool,
) -> dict[str, Any]:
    """Action panel for a single alert card."""

    toggle_label = "Выключить" if enabled else "Включить"
    toggle_action = "disable" if enabled else "enable"
    return {
        "inline_keyboard": [
            [_button(toggle_label, _cb(f"alert_{toggle_action}", token))],
            [_button("Cooldown", _cb("alert_cd", token))],
            [_button("Удалить", _cb("alert_del", token))],
            [_button("К списку", _cb("alerts", "0"))],
            [_button("В меню", _cb("home"))],
        ]
    }


def cooldown_options(token: str) -> dict[str, Any]:
    """Preset cooldown buttons + "свое значение" input branch."""

    presets = ("60", "180", "300", "600")
    rows = [
        [_button(f"{value}s", _cb("alert_cd_set", token, value))]
        for value in presets
    ]
    rows.append([_button("Другое значение", _cb("alert_cd_custom", token))])
    rows.append([_button("Отмена", _cb("alert", token))])
    return {"inline_keyboard": rows}


def confirm_delete(token: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                _button("Удалить", _cb("alert_del_yes", token)),
                _button("Отмена", _cb("alert", token)),
            ],
        ]
    }


def mute_menu() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                _button("30m", _cb("mute_set", "30m")),
                _button("2h", _cb("mute_set", "2h")),
                _button("1d", _cb("mute_set", "1d")),
            ],
            [_button("Снять тишину", _cb("unmute"))],
            [_button("В меню", _cb("home"))],
        ]
    }


def wizard_scenarios(scenarios: list[tuple[str, str]]) -> dict[str, Any]:
    """Step 1 of create-alert wizard: pick a scenario.

    ``scenarios`` is ``(short_id, label)``; ``short_id`` is the preset
    key used by the wizard state machine.
    """

    rows = [
        [_button(label, _cb("wz_scn", scenario_id))]
        for scenario_id, label in scenarios
    ]
    rows.append([_button("Отмена", _cb("wz_cancel"))])
    return {"inline_keyboard": rows}


def wizard_sensitivity() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [_button("Тихо (Conservative)", _cb("wz_sens", "conservative"))],
            [_button("Обычно (Balanced)", _cb("wz_sens", "balanced"))],
            [_button("Агрессивно (Aggressive)", _cb("wz_sens", "aggressive"))],
            [_button("Свои теги и сигналы", _cb("wz_filters_custom"))],
            [_button("Назад", _cb("wz_back"))],
            [_button("Отмена", _cb("wz_cancel"))],
        ]
    }


def wizard_custom_filters() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [_button("Пропустить (как на сервере)", _cb("wz_filters_skip"))],
            [_button("Назад", _cb("wz_back"))],
            [_button("Отмена", _cb("wz_cancel"))],
        ]
    }


def wizard_cooldown_presets() -> dict[str, Any]:
    """Cooldown step of the create wizard.

    Values mirror the :mod:`_keyboards.cooldown_options` presets so the
    create flow and the management flow stay visually consistent.
    """

    return {
        "inline_keyboard": [
            [
                _button("60s", _cb("wz_cd", "60")),
                _button("180s", _cb("wz_cd", "180")),
                _button("300s", _cb("wz_cd", "300")),
                _button("600s", _cb("wz_cd", "600")),
            ],
            [_button("Свое значение", _cb("wz_cd_custom"))],
            [_button("Назад", _cb("wz_back"))],
            [_button("Отмена", _cb("wz_cancel"))],
        ]
    }


def wizard_preview() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [_button("Создать алерт", _cb("wz_confirm"))],
            [_button("Назад", _cb("wz_back"))],
            [_button("Отмена", _cb("wz_cancel"))],
        ]
    }
