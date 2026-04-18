from __future__ import annotations

import unittest

from alarm_system.api.routes.telegram_webhook import _edit_message_or_send


class _NotModifiedEditClient:
    def __init__(self) -> None:
        self.send_calls = 0

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, object]:
        raise RuntimeError("Bad Request: message is not modified")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, object]:
        self.send_calls += 1
        return {"ok": True, "result": {"message_id": 1}}


class _OtherEditFailureClient:
    def __init__(self) -> None:
        self.send_calls = 0

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, object]:
        raise RuntimeError("Bad Request: message to edit not found")

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, object]:
        self.send_calls += 1
        return {"ok": True, "result": {"message_id": 2}}


class EditMessageFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_modified_does_not_send_duplicate(self) -> None:
        client = _NotModifiedEditClient()
        await _edit_message_or_send(
            client,
            chat_id="42",
            message_id=7,
            text="unchanged",
            reply_markup=None,
        )
        self.assertEqual(client.send_calls, 0)

    async def test_other_edit_failure_falls_back_to_send(self) -> None:
        client = _OtherEditFailureClient()
        await _edit_message_or_send(
            client,
            chat_id="42",
            message_id=7,
            text="hello",
            reply_markup=None,
        )
        self.assertEqual(client.send_calls, 1)
