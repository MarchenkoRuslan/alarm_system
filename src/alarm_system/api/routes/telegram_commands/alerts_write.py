"""Write-side alert commands: /create, /create_raw, /enable, /disable,
/delete, /set_cooldown.

All write operations are scoped to the invoking Telegram user. The
``user_id`` from the update is authoritative; users cannot target
alerts belonging to other accounts via bot commands.
"""

from __future__ import annotations

import copy
import json

from alarm_system.alert_store import (
    AlertStoreBackendError,
    AlertStoreConflictError,
    AlertStoreContractError,
)
from alarm_system.api.routes.telegram_commands._args import (
    parse_bool,
    parse_int,
)
from alarm_system.api.routes.telegram_commands._context import (
    CommandContext,
    CommandResult,
)
from alarm_system.api.routes.telegram_commands._errors import (
    BackendError,
    RuleIdentityNotAllowedError,
)
from alarm_system.api.schemas import (
    ALERT_CREATE_EXAMPLES,
    AlertCreateRequest,
)
from alarm_system.entities import Alert


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
    try:
        request = AlertCreateRequest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        return f"Некорректные данные алерта: {exc}"
    alert = request.to_alert()
    try:
        _validate_rule_identity(ctx, alert)
    except RuleIdentityNotAllowedError as exc:
        return (
            f"Правило {exc.rule_id}#{exc.rule_version} не зарегистрировано. "
            "Сервер принимает только правила из ALARM_RULES_PATH."
        )
    try:
        existing = ctx.store.get_alert(alert.alert_id)
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    if existing is not None:
        return (
            f"Алерт {alert.alert_id} уже существует. "
            "Выберите другой alert_id или используйте /enable/disable."
        )
    try:
        saved = ctx.store.upsert_alert(alert, expected_version=0)
    except AlertStoreConflictError:
        return f"Алерт {alert.alert_id} уже существует."
    except AlertStoreContractError as exc:
        return f"Не удалось создать алерт: {exc}"
    except AlertStoreBackendError as exc:
        raise BackendError(str(exc)) from exc
    return (
        f"Алерт {saved.alert_id} создан "
        f"(type={saved.alert_type.value}, cooldown={saved.cooldown_seconds}s, "
        f"enabled={saved.enabled})."
    )
