from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from alarm_system.service_runtime import (
    _interruptible_sleep,
    _run_gamma_periodic_loop,
    _sleep_gamma_interval,
)


class GammaPeriodicLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_interruptible_sleep_returns_true_when_stopped_mid_sleep(
        self,
    ) -> None:
        stop = asyncio.Event()

        async def _wake() -> None:
            await asyncio.sleep(0.02)
            stop.set()

        asyncio.create_task(_wake())
        self.assertTrue(await _interruptible_sleep(10.0, stop))

    async def test_interruptible_sleep_returns_false_on_timeout(self) -> None:
        stop = asyncio.Event()
        self.assertFalse(await _interruptible_sleep(0.01, stop))

    async def test_sleep_gamma_interval_respects_stop(self) -> None:
        stop = asyncio.Event()
        stop.set()
        self.assertTrue(
            await _sleep_gamma_interval(60.0, 0.1, stop),
        )

    async def test_gamma_periodic_loop_skips_when_already_stopped(self) -> None:
        worker = MagicMock()
        worker.poll_once = AsyncMock()
        stop = asyncio.Event()
        stop.set()
        last: dict[str, datetime | None] = {"at": None}
        await _run_gamma_periodic_loop(
            gamma_worker=worker,
            tag_ids=[1],
            interval_seconds=60,
            backoff_max_seconds=300.0,
            jitter_ratio=0.0,
            on_events=AsyncMock(),
            stop_event=stop,
            gamma_last_success_at=last,
        )
        worker.poll_once.assert_not_called()

    async def test_gamma_periodic_loop_polls_and_updates_success_timestamp(
        self,
    ) -> None:
        worker = MagicMock()
        worker.poll_once = AsyncMock(return_value=[])
        stop = asyncio.Event()
        last: dict[str, datetime | None] = {"at": None}
        seen: list[list[object]] = []

        async def on_events(events: list[object]) -> None:
            seen.append(events)
            stop.set()

        task = asyncio.create_task(
            _run_gamma_periodic_loop(
                gamma_worker=worker,
                tag_ids=[7],
                interval_seconds=0,
                backoff_max_seconds=60.0,
                jitter_ratio=0.0,
                on_events=on_events,
                stop_event=stop,
                gamma_last_success_at=last,
            )
        )
        await task
        worker.poll_once.assert_called()
        self.assertIsInstance(last.get("at"), datetime)
        self.assertEqual(len(seen), 1)


class RaisingGammaClient:
    async def fetch_markets(
        self,
        tag_ids: list[int],
        limit: int,
    ) -> list[dict[str, str]]:
        raise ConnectionError("boom")


class GammaPollErrorMetricTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_once_increments_errors_on_fetch_failure(self) -> None:
        from alarm_system.ingestion.metrics import InMemoryMetrics
        from alarm_system.ingestion.polymarket.gamma_sync import (
            GammaMetadataSyncWorker,
        )

        metrics = InMemoryMetrics()
        worker = GammaMetadataSyncWorker(
            client=RaisingGammaClient(),
            metrics=metrics,
        )
        with self.assertRaises(ConnectionError):
            await worker.poll_once(tag_ids=[1])
        self.assertEqual(
            metrics.snapshot().counters.get("ingestion.gamma.poll_errors_total"),
            1,
        )
