from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alarm_system.alert_store import AlertStore
from alarm_system.api.routes.telegram_commands import (
    AlertNotFoundError,
    BackendError,
    CommandContext,
    build_command_registry,
    split_command,
)
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.state import (
    DeliveryAttemptStore,
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
    MuteStore,
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

    text: str | None = None
    chat: TelegramChat
    from_: TelegramUser | None = Field(default=None, alias="from")


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage | None = None


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
) -> None:
    try:
        await telegram_client.send_message(
            chat_id=chat_id,
            text=_truncate_for_telegram(text),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"telegram send failed: {exc}",
        ) from exc


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
    if not text.strip().startswith("/"):
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


def _build_context(
    *,
    store: AlertStore,
    telegram_client: TelegramApiClient,
    mute_store: MuteStore,
    attempt_store: DeliveryAttemptStore,
    user_id: str,
    chat_id: str,
    text: str,
    rule_identities: frozenset[tuple[str, int]] | None,
) -> CommandContext:
    return CommandContext(
        store=store,
        telegram_client=telegram_client,
        mute_store=mute_store,
        attempt_store=attempt_store,
        user_id=user_id,
        chat_id=chat_id,
        args=split_command(text),
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


def build_telegram_router(
    *,
    store: AlertStore,
    telegram_client: TelegramApiClient,
    mute_store: MuteStore | None = None,
    attempt_store: DeliveryAttemptStore | None = None,
    secret_token: str | None = None,
    rule_identities: set[tuple[str, int]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["telegram"])
    resolved_mute_store = mute_store or InMemoryMuteStore()
    resolved_attempt_store = attempt_store or InMemoryDeliveryAttemptStore()
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
        extracted = await _extract_command_input(
            payload=payload,
            telegram_client=telegram_client,
        )
        if extracted is None:
            return {"ok": True}
        text, chat_id, user_id = extracted
        ctx = _build_context(
            store=store,
            telegram_client=telegram_client,
            mute_store=resolved_mute_store,
            attempt_store=resolved_attempt_store,
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            rule_identities=resolved_rule_identities,
        )
        response_text = await _run_command(ctx=ctx, registry=registry)

        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=response_text,
        )
        return {"ok": True}

    return router
