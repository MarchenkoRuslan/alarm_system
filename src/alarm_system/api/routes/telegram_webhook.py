from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from alarm_system.alert_store import AlertStore, AlertStoreBackendError
from alarm_system.api.telegram_client import TelegramApiClient
from alarm_system.entities import ChannelBinding, DeliveryChannel


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


async def _handle_start(
    store: AlertStore,
    telegram_client: TelegramApiClient,
    user_id: str,
    chat_id: str,
) -> dict[str, bool]:
    binding = ChannelBinding.model_validate(
        {
            "binding_id": f"tg-{user_id}-{chat_id}",
            "user_id": user_id,
            "channel": DeliveryChannel.TELEGRAM,
            "destination": chat_id,
            "is_verified": True,
        }
    )
    try:
        store.upsert_binding(binding)
    except AlertStoreBackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await telegram_client.send_message(
        chat_id=chat_id,
        text=(
            "Привет. Я подключен и могу отправлять алерты.\n"
            "Команды: /help, /alerts"
        ),
    )
    return {"ok": True}


async def _handle_help(
    telegram_client: TelegramApiClient,
    chat_id: str,
) -> dict[str, bool]:
    await telegram_client.send_message(
        chat_id=chat_id,
        text=(
            "Доступные команды:\n"
            "/start - привязать текущий чат\n"
            "/alerts - показать активные алерты"
        ),
    )
    return {"ok": True}


async def _handle_alerts(
    store: AlertStore,
    telegram_client: TelegramApiClient,
    user_id: str,
    chat_id: str,
) -> dict[str, bool]:
    try:
        alerts = store.list_alerts(user_id=user_id, include_disabled=False)
    except AlertStoreBackendError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not alerts:
        message = "Активных алертов пока нет."
    else:
        lines = ["Ваши активные алерты:"]
        for alert in alerts[:20]:
            lines.append(
                f"- {alert.alert_id}: {alert.alert_type.value}, "
                f"cooldown={alert.cooldown_seconds}s"
            )
        message = "\n".join(lines)
    await telegram_client.send_message(chat_id=chat_id, text=message)
    return {"ok": True}


async def _handle_unknown(
    telegram_client: TelegramApiClient,
    chat_id: str,
) -> dict[str, bool]:
    await telegram_client.send_message(
        chat_id=chat_id,
        text="Неизвестная команда. Используйте /help.",
    )
    return {"ok": True}


def build_telegram_router(
    *,
    store: AlertStore,
    telegram_client: TelegramApiClient,
    secret_token: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["telegram"])

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

        text = payload.message.text.strip()
        chat_id = str(payload.message.chat.id)
        user = payload.message.from_
        user_id = str(user.id) if user is not None else chat_id

        if text.startswith("/start"):
            return await _handle_start(store, telegram_client, user_id, chat_id)
        if text.startswith("/help"):
            return await _handle_help(telegram_client, chat_id)
        if text.startswith("/alerts"):
            return await _handle_alerts(store, telegram_client, user_id, chat_id)
        return await _handle_unknown(telegram_client, chat_id)

    return router
