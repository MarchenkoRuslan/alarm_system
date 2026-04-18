"""Alert slash commands — both read and write sides.

Read side (``/alerts``, ``/alert``, ``/templates``) is a thin shell over
the interactive card/list UI in :mod:`_ui` — slash commands are now
entry points to paginated keyboards rather than plain-text dumps. The
``--all`` flag on ``/alerts`` keeps the legacy scriptable output for
power users.

Write side (``/create``, ``/create_raw``, ``/enable``, ``/disable``,
``/delete``, ``/set_cooldown``) is scoped to the invoking Telegram user:
the ``user_id`` from the update is authoritative and users cannot
target alerts belonging to other accounts via bot commands.
"""

from __future__ import annotations

import copy
import json

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreConflictError,
    AlertStoreContractError,
)
from alarm_system.api.alert_presets import ALERT_CREATE_EXAMPLES
from alarm_system.api.routes.telegram_commands import _keyboards, _ui
from alarm_system.api.routes.telegram_commands._args import (
    parse_bool,
    parse_int,
)
from alarm_system.api.routes.telegram_commands._context import (
    BackendError,
    CommandContext,
    CommandResult,
    RuleIdentityNotAllowedError,
)
from alarm_system.api.schemas import AlertCreateRequest
from alarm_system.entities import Alert


# --------------------------------------------------------------------------
# Read side: /alerts, /alert, /templates.
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Write side: /enable, /disable, /set_cooldown, /delete, /create, /create_raw.
# --------------------------------------------------------------------------


_CONFLICT_MESSAGE = (
    "Алерт был изменен параллельно, попробуйте команду еще раз."
)


def _upsert_update(ctx: CommandContext, alert: Alert) -> Alert | str:
    """Run an ``upsert_alert`` update with optimistic concurrency control.

    Returns the saved ``Alert`` on success, or a user-facing string on
    logical failure (version conflict / contract violation). Backend
    outages escape as ``BackendError`` so the dispatcher can map them
    to HTTP 503.
    """

    try:
        return ctx.store.upsert_alert(alert, expected_version=alert.version)
    except AlertStoreConflictError:
        return _CONFLICT_MESSAGE
    except AlertStoreContractError as exc:
        return f"Не удалось обновить алерт: {exc}"
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc


def _validate_rule_identity(ctx: CommandContext, alert: Alert) -> None:
    """Reject alerts referencing unknown rules when whitelist is active."""

    if ctx.rule_identities is None:
        return
    identity = (alert.rule_id, alert.rule_version)
    if identity not in ctx.rule_identities:
        raise RuleIdentityNotAllowedError(alert.rule_id, alert.rule_version)


async def handle_enable(ctx: CommandContext) -> CommandResult:
    return await _toggle_enabled(ctx, target=True)


async def handle_disable(ctx: CommandContext) -> CommandResult:
    return await _toggle_enabled(ctx, target=False)


def _enabled_label(target: bool) -> str:
    return "включен" if target else "выключен"


async def _toggle_enabled(ctx: CommandContext, *, target: bool) -> CommandResult:
    alert_id = ctx.args.first_positional()
    if not alert_id:
        verb = "включить" if target else "выключить"
        return (
            f"Используйте: /{'enable' if target else 'disable'} "
            f"<alert_id> — {verb}"
        )
    found = ctx.fetch_owned_alert(alert_id)
    if found.enabled is target:
        return f"Алерт {alert_id} уже {_enabled_label(target)}."
    saved = _upsert_update(ctx, found.model_copy(update={"enabled": target}))
    if isinstance(saved, str):
        return saved
    return (
        f"Алерт {alert_id} {_enabled_label(target)}. "
        f"Новая версия: {saved.version}."
    )


async def handle_set_cooldown(ctx: CommandContext) -> CommandResult:
    positional = ctx.args.positional
    if len(positional) < 2:
        return "Используйте: /set_cooldown <alert_id> <seconds>"
    alert_id = positional[0]
    try:
        cooldown = parse_int(positional[1])
    except ValueError:
        return f"Некорректное число секунд: {positional[1]!r}"
    if cooldown < 0:
        return "Cooldown должен быть неотрицательным."
    found = ctx.fetch_owned_alert(alert_id)
    if found.cooldown_seconds == cooldown:
        return f"Cooldown уже равен {cooldown}s."
    saved = _upsert_update(
        ctx,
        found.model_copy(update={"cooldown_seconds": cooldown}),
    )
    if isinstance(saved, str):
        return saved
    return (
        f"Cooldown алерта {alert_id} обновлен на {cooldown}s. "
        f"Новая версия: {saved.version}."
    )


async def handle_delete(ctx: CommandContext) -> CommandResult:
    positional = ctx.args.positional
    if not positional:
        return "Используйте: /delete <alert_id> [yes]"
    alert_id = positional[0]
    confirmed = len(positional) > 1 and positional[1].lower() == "yes"
    ctx.fetch_owned_alert(alert_id)
    if not confirmed:
        return (
            f"Подтвердите удаление алерта {alert_id}: "
            f"отправьте `/delete {alert_id} yes`."
        )
    try:
        deleted = ctx.store.delete_alert(alert_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if not deleted:
        return f"Алерт {alert_id} уже был удален."
    return f"Алерт {alert_id} удален."


async def handle_create(ctx: CommandContext) -> CommandResult:
    template_id = ctx.args.first_positional()
    if not template_id:
        return (
            "Используйте: /create <template_id> [alert_id=...] "
            "[cooldown=...] [enabled=true|false]. "
            "Список шаблонов: /templates."
        )
    template = ALERT_CREATE_EXAMPLES.get(template_id)
    if template is None:
        return f"Неизвестный шаблон {template_id!r}. См. /templates."
    payload = copy.deepcopy(template["value"])
    payload["user_id"] = ctx.user_id
    # Default to enabled: users invoking /create expect their alert to
    # start firing immediately. Templates in Swagger default to False
    # for demo hygiene; the bot's UX inverts that.
    payload["enabled"] = True
    _apply_create_overrides_alert_id(payload, ctx.args.option("alert_id"))
    error = _apply_create_overrides_cooldown(
        payload,
        ctx.args.option("cooldown"),
    )
    if error is not None:
        return error
    error = _apply_create_overrides_enabled(
        payload,
        ctx.args.option("enabled"),
    )
    if error is not None:
        return error
    return await _create_from_payload(ctx, payload)


def _apply_create_overrides_alert_id(
    payload: dict,
    alert_id_override: str | None,
) -> None:
    if alert_id_override:
        payload["alert_id"] = alert_id_override
    else:
        payload.pop("alert_id", None)


def _apply_create_overrides_cooldown(
    payload: dict,
    cooldown_raw: str | None,
) -> str | None:
    if cooldown_raw is None:
        return None
    try:
        cooldown_value = parse_int(cooldown_raw)
    except ValueError:
        return f"Некорректный cooldown: {cooldown_raw!r}"
    if cooldown_value < 0:
        return "Cooldown должен быть неотрицательным."
    payload["cooldown_seconds"] = cooldown_value
    return None


def _apply_create_overrides_enabled(
    payload: dict,
    enabled_raw: str | None,
) -> str | None:
    if enabled_raw is None:
        return None
    try:
        payload["enabled"] = parse_bool(enabled_raw)
    except ValueError:
        return f"Некорректный enabled: {enabled_raw!r}"
    return None


async def handle_create_raw(ctx: CommandContext) -> CommandResult:
    raw_tail = ctx.args.raw_tail.strip()
    if not raw_tail:
        return (
            "Используйте: /create_raw <json>. "
            "Формат — как у POST /internal/alerts."
        )
    try:
        payload = json.loads(raw_tail)
    except json.JSONDecodeError as exc:
        return f"Не удалось прочитать JSON: {exc}"
    if not isinstance(payload, dict):
        return "JSON должен быть объектом."
    payload["user_id"] = ctx.user_id
    return await _create_from_payload(ctx, payload)


async def _create_from_payload(
    ctx: CommandContext,
    payload: dict,
) -> CommandResult:
    """Validate, persist, and return a human-readable result string.

    Returns the success message string on success, or a user-facing
    error string on any logical failure (validation, rule-identity
    rejection, conflict). Backend store outages escape as
    :class:`BackendError` so the webhook dispatcher maps them to 503.

    Callers that need to distinguish success from failure (e.g. the
    wizard's ``_finalise``) should use :func:`_create_alert_or_error`
    instead, which returns ``Alert | str``.

    Kept ``async`` for API compatibility with callers that already
    ``await`` it; the underlying work is synchronous.
    """

    result = _create_alert_or_error(ctx, payload)
    if isinstance(result, Alert):
        return (
            f"Алерт {result.alert_id} создан "
            f"(type={result.alert_type.value}, cooldown={result.cooldown_seconds}s, "
            f"enabled={result.enabled})."
        )
    return result


def _create_alert_or_error(
    ctx: CommandContext,
    payload: dict,
) -> Alert | str:
    """Core create pipeline that returns ``Alert`` on success, str on failure.

    The entire pipeline is synchronous: the store calls are blocking
    by design (``AlertStore`` protocol does not expose async methods).
    Declared as a plain function — not ``async`` — so callers do not
    create a needless coroutine object.

    Used by :func:`_create_from_payload` for slash commands and
    directly by the wizard's ``_finalise`` so each call site can
    distinguish a persisted alert from a user-facing error string
    without string-parsing the result.
    """

    parsed = _alert_from_payload(payload)
    if isinstance(parsed, str):
        return parsed
    rule_error = _rule_identity_error(ctx, parsed)
    if rule_error is not None:
        return rule_error
    exists_error = _alert_exists_error(ctx, parsed.alert_id)
    if exists_error is not None:
        return exists_error
    return _create_alert(ctx, parsed)


def _alert_from_payload(payload: dict) -> Alert | str:
    try:
        request = AlertCreateRequest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        return f"Некорректные данные алерта: {exc}"
    return request.to_alert()


def _rule_identity_error(
    ctx: CommandContext,
    alert: Alert,
) -> str | None:
    try:
        _validate_rule_identity(ctx, alert)
    except RuleIdentityNotAllowedError as exc:
        return (
            f"Правило {exc.rule_id}#{exc.rule_version} не зарегистрировано. "
            "Сервер принимает только правила из ALARM_RULES_PATH."
        )
    return None


def _alert_exists_error(ctx: CommandContext, alert_id: str) -> str | None:
    try:
        existing = ctx.store.get_alert(alert_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if existing is None:
        return None
    return (
        f"Алерт {alert_id} уже существует. "
        "Выберите другой alert_id или используйте /enable/disable."
    )


def _create_alert(ctx: CommandContext, alert: Alert) -> Alert | str:
    try:
        return ctx.store.upsert_alert(alert, expected_version=0)
    except AlertStoreConflictError:
        return f"Алерт {alert.alert_id} уже существует."
    except AlertStoreContractError as exc:
        return f"Не удалось создать алерт: {exc}"
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
