from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alarm_system.alert_store import AlertStore
from alarm_system.api.routes.telegram_commands import (
    AlertNotFoundError,
    BackendError,
    CommandArgs,
    CommandContext,
    build_command_registry,
    split_command,
)
from alarm_system.api.routes.telegram_commands._callbacks import (
    CallbackResult,
    dispatch_callback,
    handle_pending_text_input,
)
from alarm_system.api.routes.telegram_commands._keyboards import parse_callback
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.state import (
    DeliveryAttemptStore,
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
    InMemorySessionStore,
    MuteStore,
    SessionStore,
)


logger = logging.getLogger(__name__)


# Telegram message hard cap is 4096 characters. We reject inputs above
# this threshold (likely noise or abuse) and truncate outgoing replies
# slightly below so we always fit within Bot API limits.
_MAX_INCOMING_TEXT = 4096
_MAX_OUTGOING_TEXT = 3900
_TRUNCATION_SUFFIX = "\n…обрезано"
_GROUP_CHAT_HINT = (
    "Бот работает только в приватных чатах. "
    "Откройте личный диалог с ботом и отправьте /start."
)


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int | None = None
    text: str | None = None
    chat: TelegramChat
    from_: TelegramUser | None = Field(default=None, alias="from")


class TelegramCallbackQuery(BaseModel):
    """Minimal callback query payload.

    ``message`` is optional per Bot API: the bot receives the message
    the button was attached to only if it was sent by the bot itself.
    In our own UX we only emit buttons from bot messages so the field
    is effectively always present, but we keep it optional to match the
    spec and gracefully ignore malformed updates.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    data: str | None = None
    message: TelegramMessage | None = None
    from_: TelegramUser | None = Field(default=None, alias="from")


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _MAX_OUTGOING_TEXT:
        return text
    cut = _MAX_OUTGOING_TEXT - len(_TRUNCATION_SUFFIX)
    return text[:cut] + _TRUNCATION_SUFFIX


async def _send_message_or_502(
    telegram_client: TelegramApiClient,
    *,
    chat_id: str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    try:
        await telegram_client.send_message(
            chat_id=chat_id,
            text=_truncate_for_telegram(text),
            reply_markup=reply_markup,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"telegram send failed: {exc}",
        ) from exc


async def _edit_message_or_send(
    telegram_client: TelegramApiClient,
    *,
    chat_id: str,
    message_id: int | None,
    text: str,
    reply_markup: dict[str, Any] | None,
) -> None:
    """Prefer in-place edit to keep the UI stateful; fall back to send.

    If Telegram rejects the edit because the payload is unchanged
    (``message is not modified``), we return without sending a duplicate.
    For other edit failures (e.g. message too old, not found), we send a
    fresh message so the user still sees the update.
    """

    truncated = _truncate_for_telegram(text)
    if message_id is not None:
        try:
            await telegram_client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=truncated,
                reply_markup=reply_markup,
            )
            return
        except Exception as exc:  # noqa: BLE001
            if "message is not modified" in str(exc).lower():
                logger.info(
                    "telegram_edit_message_noop",
                    extra={
                        "chat_id": chat_id,
                        "message_id": message_id,
                    },
                )
                return
            logger.info(
                "telegram_edit_message_fallback_to_send",
                extra={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "error": str(exc),
                },
            )
    await _send_message_or_502(
        telegram_client,
        chat_id=chat_id,
        text=truncated,
        reply_markup=reply_markup,
    )


async def _answer_callback(
    telegram_client: TelegramApiClient,
    *,
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    try:
        await telegram_client.answer_callback_query(
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "telegram_answer_callback_failed",
            extra={"callback_query_id": callback_query_id, "error": str(exc)},
        )


def _validate_webhook_secret(
    *,
    secret_token: str | None,
    provided_secret: str | None,
) -> None:
    if secret_token is None:
        return
    if provided_secret == secret_token:
        return
    raise HTTPException(
        status_code=401,
        detail="invalid telegram webhook secret",
    )


async def _extract_command_input(
    *,
    payload: TelegramUpdate,
    telegram_client: TelegramApiClient,
) -> tuple[str, str, str] | None:
    if payload.message is None or payload.message.text is None:
        return None
    text = payload.message.text
    if len(text) > _MAX_INCOMING_TEXT:
        logger.warning(
            "telegram_webhook_text_too_long",
            extra={"length": len(text)},
        )
        return None
    user = payload.message.from_
    if user is None:
        return None
    chat_id = str(payload.message.chat.id)
    if payload.message.chat.id <= 0:
        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=_GROUP_CHAT_HINT,
        )
        return None
    return text, chat_id, str(user.id)


_EMPTY_ARGS = CommandArgs(command="")


def _build_context(
    *,
    store: AlertStore,
    telegram_client: TelegramApiClient,
    mute_store: MuteStore,
    attempt_store: DeliveryAttemptStore,
    session_store: SessionStore,
    user_id: str,
    chat_id: str,
    args: CommandArgs = _EMPTY_ARGS,
    rule_identities: frozenset[tuple[str, int]] | None,
) -> CommandContext:
    """Build a :class:`CommandContext` for any of the webhook branches.

    Slash commands pass real ``CommandArgs``; callback-query and
    pending-text-input flows do not carry command args and fall back
    to the shared empty sentinel. Keeping ``args`` typed (rather than
    accepting raw text and calling :func:`split_command` internally)
    makes that intent explicit at the call site.
    """

    return CommandContext(
        store=store,
        telegram_client=telegram_client,
        mute_store=mute_store,
        attempt_store=attempt_store,
        session_store=session_store,
        user_id=user_id,
        chat_id=chat_id,
        args=args,
        rule_identities=rule_identities,
    )


async def _run_command(
    *,
    ctx: CommandContext,
    registry: dict,
) -> str:
    handler = registry.get(ctx.args.command)
    if handler is None:
        return "Неизвестная команда. Используйте /help."
    try:
        return await handler(ctx)
    except AlertNotFoundError as exc:
        return f"Алерт {exc.alert_id} не найден."
    except BackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _handle_callback_query(
    *,
    callback: TelegramCallbackQuery,
    telegram_client: TelegramApiClient,
    store: AlertStore,
    mute_store: MuteStore,
    attempt_store: DeliveryAttemptStore,
    session_store: SessionStore,
    rule_identities: frozenset[tuple[str, int]] | None,
) -> None:
    if (
        callback.message is None
        or callback.from_ is None
        or callback.data is None
    ):
        await _answer_callback(
            telegram_client,
            callback_query_id=callback.id,
            text="Кнопка устарела",
        )
        return
    chat_id = str(callback.message.chat.id)
    if callback.message.chat.id <= 0:
        await _answer_callback(
            telegram_client,
            callback_query_id=callback.id,
            text="Только в приватном чате",
        )
        return
    parsed = parse_callback(callback.data)
    if parsed is None:
        await _answer_callback(
            telegram_client,
            callback_query_id=callback.id,
            text="Кнопка устарела, откройте /start",
        )
        return
    action, args = parsed
    ctx = _build_context(
        store=store,
        telegram_client=telegram_client,
        mute_store=mute_store,
        attempt_store=attempt_store,
        session_store=session_store,
        user_id=str(callback.from_.id),
        chat_id=chat_id,
        rule_identities=rule_identities,
    )
    try:
        result = await dispatch_callback(ctx, action, args)
    except AlertNotFoundError as exc:
        result = CallbackResult(toast=f"Алерт {exc.alert_id} не найден")
    except BackendError as exc:
        await _answer_callback(
            telegram_client,
            callback_query_id=callback.id,
            text="Сервис недоступен, попробуйте позже",
            show_alert=True,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await _answer_callback(
        telegram_client,
        callback_query_id=callback.id,
        text=result.toast,
        show_alert=result.show_alert,
    )
    if result.text is not None:
        await _edit_message_or_send(
            telegram_client,
            chat_id=chat_id,
            message_id=callback.message.message_id,
            text=result.text,
            reply_markup=result.reply_markup,
        )


async def _handle_pending_input_or_none(
    *,
    text: str,
    chat_id: str,
    user_id: str,
    telegram_client: TelegramApiClient,
    store: AlertStore,
    mute_store: MuteStore,
    attempt_store: DeliveryAttemptStore,
    session_store: SessionStore,
    rule_identities: frozenset[tuple[str, int]] | None,
) -> bool:
    """Return ``True`` when a wizard/text-input slot consumed the message."""

    ctx = _build_context(
        store=store,
        telegram_client=telegram_client,
        mute_store=mute_store,
        attempt_store=attempt_store,
        session_store=session_store,
        user_id=user_id,
        chat_id=chat_id,
        rule_identities=rule_identities,
    )
    try:
        result = await handle_pending_text_input(ctx, text)
    except AlertNotFoundError as exc:
        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=f"Алерт {exc.alert_id} не найден.",
        )
        return True
    except BackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        return False
    # For text-input flows we cannot edit the previous message (we
    # don't have its message_id), so always send a fresh one.
    if result.text is not None:
        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=result.text,
            reply_markup=result.reply_markup,
        )
    return True


def build_telegram_router(
    *,
    store: AlertStore,
    telegram_client: TelegramApiClient,
    mute_store: MuteStore | None = None,
    attempt_store: DeliveryAttemptStore | None = None,
    session_store: SessionStore | None = None,
    secret_token: str | None = None,
    rule_identities: set[tuple[str, int]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["telegram"])
    resolved_mute_store = mute_store or InMemoryMuteStore()
    resolved_attempt_store = attempt_store or InMemoryDeliveryAttemptStore()
    resolved_session_store = session_store or InMemorySessionStore()
    resolved_rule_identities: frozenset[tuple[str, int]] | None = (
        frozenset(rule_identities) if rule_identities is not None else None
    )
    registry = build_command_registry()

    @router.post("/telegram")
    async def telegram_webhook(
        payload: TelegramUpdate,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _validate_webhook_secret(
            secret_token=secret_token,
            provided_secret=x_telegram_bot_api_secret_token,
        )
        if payload.callback_query is not None:
            await _handle_callback_query(
                callback=payload.callback_query,
                telegram_client=telegram_client,
                store=store,
                mute_store=resolved_mute_store,
                attempt_store=resolved_attempt_store,
                session_store=resolved_session_store,
                rule_identities=resolved_rule_identities,
            )
            return {"ok": True}
        extracted = await _extract_command_input(
            payload=payload,
            telegram_client=telegram_client,
        )
        if extracted is None:
            return {"ok": True}
        text, chat_id, user_id = extracted
        if not text.strip().startswith("/"):
            # Non-slash text: try pending-input / wizard consumer.
            consumed = await _handle_pending_input_or_none(
                text=text,
                chat_id=chat_id,
                user_id=user_id,
                telegram_client=telegram_client,
                store=store,
                mute_store=resolved_mute_store,
                attempt_store=resolved_attempt_store,
                session_store=resolved_session_store,
                rule_identities=resolved_rule_identities,
            )
            if not consumed:
                logger.debug(
                    "telegram_webhook_ignoring_free_form",
                    extra={"chat_id": chat_id},
                )
            return {"ok": True}
        ctx = _build_context(
            store=store,
            telegram_client=telegram_client,
            mute_store=resolved_mute_store,
            attempt_store=resolved_attempt_store,
            session_store=resolved_session_store,
            user_id=user_id,
            chat_id=chat_id,
            args=split_command(text),
            rule_identities=resolved_rule_identities,
        )
        response_text = await _run_command(ctx=ctx, registry=registry)

        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=response_text,
            reply_markup=ctx.reply_markup,
        )
        return {"ok": True}

    return router
