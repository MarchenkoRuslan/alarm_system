from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from alarm_system.ingestion.polymarket.gamma_periodic import (
    interruptible_sleep,
    run_gamma_periodic_loop,
    sleep_gamma_interval,
)


def _noop_emit(_kind: str, _payload: dict) -> None:
    pass


class GammaPeriodicLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_interruptible_sleep_returns_true_when_stopped_mid_sleep(
        self,
    ) -> None:
        stop = asyncio.Event()

        async def _wake() -> None:
            await asyncio.sleep(0.02)
            stop.set()

        asyncio.create_task(_wake())
        self.assertTrue(await interruptible_sleep(10.0, stop))

    async def test_interruptible_sleep_returns_false_on_timeout(self) -> None:
        stop = asyncio.Event()
        self.assertFalse(await interruptible_sleep(0.01, stop))

    async def test_sleep_gamma_interval_respects_stop(self) -> None:
        stop = asyncio.Event()
        stop.set()
        self.assertTrue(
            await sleep_gamma_interval(60.0, 0.1, stop),
        )

    async def test_gamma_periodic_loop_skips_when_already_stopped(self) -> None:
        worker = MagicMock()
        worker.poll_once = AsyncMock()
        stop = asyncio.Event()
        stop.set()
        last: dict[str, datetime | None] = {"at": None}
        await run_gamma_periodic_loop(
            gamma_worker=worker,
            tag_ids=[1],
            interval_seconds=60,
            backoff_max_seconds=300.0,
            jitter_ratio=0.0,
            on_events=AsyncMock(),
            stop_event=stop,
            gamma_last_success_at=last,
            emit_log=_noop_emit,
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
            run_gamma_periodic_loop(
                gamma_worker=worker,
                tag_ids=[7],
                interval_seconds=0,
                backoff_max_seconds=60.0,
                jitter_ratio=0.0,
                on_events=on_events,
                stop_event=stop,
                gamma_last_success_at=last,
                emit_log=_noop_emit,
            )
        )
        await task
        worker.poll_once.assert_called()
        self.assertIsInstance(last.get("at"), datetime)
        self.assertEqual(len(seen), 1)

    async def test_gamma_periodic_loop_cancelled_poll_does_not_emit_fetch_log(
        self,
    ) -> None:
        worker = MagicMock()
        worker.poll_once = AsyncMock(side_effect=asyncio.CancelledError())
        stop = asyncio.Event()
        last: dict[str, datetime | None] = {"at": None}
        emitted: list[tuple[str, dict]] = []

        def emit(kind: str, payload: dict) -> None:
            emitted.append((kind, payload))

        with self.assertRaises(asyncio.CancelledError):
            await run_gamma_periodic_loop(
                gamma_worker=worker,
                tag_ids=[1],
                interval_seconds=0,
                backoff_max_seconds=60.0,
                jitter_ratio=0.0,
                on_events=AsyncMock(),
                stop_event=stop,
                gamma_last_success_at=last,
                emit_log=emit,
            )
        self.assertEqual(emitted, [])

    async def test_gamma_periodic_loop_pipeline_error_does_not_update_timestamp(
        self,
    ) -> None:
        worker = MagicMock()
        worker.poll_once = AsyncMock(return_value=[])
        stop = asyncio.Event()
        last: dict[str, datetime | None] = {"at": None}
        emitted: list[tuple[str, dict]] = []

        def emit(kind: str, payload: dict) -> None:
            emitted.append((kind, payload))

        async def on_events(_events: list[object]) -> None:
            raise RuntimeError("pipeline boom")

        with self.assertRaises(RuntimeError):
            await run_gamma_periodic_loop(
                gamma_worker=worker,
                tag_ids=[1],
                interval_seconds=0,
                backoff_max_seconds=60.0,
                jitter_ratio=0.0,
                on_events=on_events,
                stop_event=stop,
                gamma_last_success_at=last,
                emit_log=emit,
            )
        self.assertIsNone(last.get("at"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "gamma_pipeline_error")
        self.assertEqual(emitted[0][1].get("phase"), "on_events")


class RaisingGammaClient:
    async def fetch_markets(
        self,
        tag_ids: list[int],
        limit: int,
    ) -> list[dict[str, str]]:
        raise ConnectionError("boom")


class GammaPollErrorMetricTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_once_cancelled_error_does_not_increment_errors(
        self,
    ) -> None:
        class CancellingClient:
            async def fetch_markets(
                self,
                tag_ids: list[int],
                limit: int,
            ) -> list[dict[str, str]]:
                raise asyncio.CancelledError()

        from alarm_system.ingestion.metrics import InMemoryMetrics
        from alarm_system.ingestion.polymarket.gamma_sync import (
            GammaMetadataSyncWorker,
        )

        metrics = InMemoryMetrics()
        worker = GammaMetadataSyncWorker(
            client=CancellingClient(),
            metrics=metrics,
        )
        with self.assertRaises(asyncio.CancelledError):
            await worker.poll_once(tag_ids=[1])
        self.assertIsNone(
            metrics.snapshot().counters.get("ingestion.gamma.poll_errors_total"),
        )

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
