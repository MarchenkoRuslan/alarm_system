from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class WsConnection(Protocol):
    async def send(self, data: str) -> None:
        ...

    async def recv(self) -> str:
        ...

    async def close(self) -> None:
        ...


class WsConnector(Protocol):
    async def connect(self, url: str) -> WsConnection:
        ...


class WebsocketsConnector:
    async def connect(self, url: str) -> WsConnection:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "The 'websockets' package is required to run Polymarket WS ingestion."
            ) from exc
        return await websockets.connect(url, ping_interval=None)  # type: ignore[return-value]


@dataclass(frozen=True)
class PolymarketWsConfig:
    url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketWsClient:
    def __init__(
        self,
        config: PolymarketWsConfig | None = None,
        connector: WsConnector | None = None,
    ) -> None:
        self._config = config or PolymarketWsConfig()
        self._connector = connector or WebsocketsConnector()
        self._connection: WsConnection | None = None

    async def connect(self) -> None:
        self._connection = await self._connector.connect(self._config.url)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def subscribe_market(self, asset_ids: list[str]) -> None:
        await self._send_json(
            {
                "type": "market",
                "assets_ids": asset_ids,
            }
        )

    async def send_ping(self) -> None:
        await self._send_json({"type": "PING"})

    async def recv_json(self) -> dict[str, Any]:
        raw = await self._require_connection().recv()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object from WS stream")
        return payload

    async def _send_json(self, payload: dict[str, Any]) -> None:
        await self._require_connection().send(json.dumps(payload))

    def _require_connection(self) -> WsConnection:
        if self._connection is None:
            raise RuntimeError("WS client is not connected")
        return self._connection
