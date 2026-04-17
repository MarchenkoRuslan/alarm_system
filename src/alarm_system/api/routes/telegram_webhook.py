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
        if (
            secret_token is not None
            and x_telegram_bot_api_secret_token != secret_token
        ):
            raise HTTPException(
                status_code=401,
                detail="invalid telegram webhook secret",
            )
        if payload.message is None or payload.message.text is None:
            return {"ok": True}

        text = payload.message.text
        if len(text) > _MAX_INCOMING_TEXT:
            logger.warning(
                "telegram_webhook_text_too_long",
                extra={"length": len(text)},
            )
            return {"ok": True}

        stripped = text.strip()
        # Silent no-op for non-command noise (stickers w/ caption, regular
        # greetings, service updates) — otherwise the bot would spam
        # "unknown command" at every chit-chat.
        if not stripped.startswith("/"):
            return {"ok": True}

        chat_id = str(payload.message.chat.id)
        user = payload.message.from_
        # Channel posts / service updates come without a ``from_``; they
        # would otherwise pollute storage with chat_id as user_id.
        if user is None:
            return {"ok": True}
        # Private chats have positive chat ids; groups and channels are
        # negative. The bot is designed for per-user state, so refuse
        # group contexts with a one-time hint and a silent ack after.
        if payload.message.chat.id <= 0:
            await _send_message_or_502(
                telegram_client,
                chat_id=chat_id,
                text=_GROUP_CHAT_HINT,
            )
            return {"ok": True}
        user_id = str(user.id)

        args = split_command(text)
        handler = registry.get(args.command)
        if handler is None:
            response_text = "Неизвестная команда. Используйте /help."
        else:
            ctx = CommandContext(
                store=store,
                telegram_client=telegram_client,
                mute_store=resolved_mute_store,
                attempt_store=resolved_attempt_store,
                user_id=user_id,
                chat_id=chat_id,
                args=args,
                rule_identities=resolved_rule_identities,
            )
            try:
                response_text = await handler(ctx)
            except AlertNotFoundError as exc:
                response_text = f"Алерт {exc.alert_id} не найден."
            except BackendError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        await _send_message_or_502(
            telegram_client,
            chat_id=chat_id,
            text=response_text,
        )
        return {"ok": True}

    return router
