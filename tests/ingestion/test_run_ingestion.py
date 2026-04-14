from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from alarm_system.ingestion.run_ingestion import IngestionRuntimeConfig, run


class _FakeWsClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeSupervisor:
    def __init__(self) -> None:
        self.received_stop_event: asyncio.Event | None = None
        self.stop_observed = False

    async def run(self, on_events, stop_event: asyncio.Event) -> None:  # noqa: ANN001
        self.received_stop_event = stop_event
        while not stop_event.is_set():
            await asyncio.sleep(0.01)
        self.stop_observed = True


class _FakeGammaWorker:
    async def poll_once(self, tag_ids):  # noqa: ANN001
        return []


class RunIngestionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_run_sets_stop_event_for_graceful_shutdown(self) -> None:
        fake_ws_client = _FakeWsClient()
        fake_supervisor = _FakeSupervisor()
        runtime_config = IngestionRuntimeConfig(asset_ids=["asset-1"], gamma_tag_ids=[])

        with patch(
            "alarm_system.ingestion.run_ingestion.PolymarketMarketAdapter",
            return_value=object(),
        ), patch(
            "alarm_system.ingestion.run_ingestion.PolymarketWsClient",
            return_value=fake_ws_client,
        ), patch(
            "alarm_system.ingestion.run_ingestion.PolymarketIngestionSupervisor",
            return_value=fake_supervisor,
        ), patch(
            "alarm_system.ingestion.run_ingestion.GammaMetadataSyncWorker",
            return_value=_FakeGammaWorker(),
        ):
            task = asyncio.create_task(run(runtime_config))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertIsNotNone(fake_supervisor.received_stop_event)
        self.assertTrue(fake_supervisor.stop_observed)
        self.assertTrue(fake_ws_client.closed)
