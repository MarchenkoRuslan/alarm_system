from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from alarm_system.ingestion.polymarket.gamma_sync import GammaMetadataSyncWorker


async def interruptible_sleep(
    seconds: float, stop_event: asyncio.Event
) -> bool:
    """Return True if ``stop_event`` was set during the sleep."""
    if seconds <= 0:
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def sleep_gamma_interval(
    interval_seconds: float,
    jitter_ratio: float,
    stop_event: asyncio.Event,
) -> bool:
    """Sleep one Gamma poll interval with jitter. Returns True if stopped."""
    jitter = random.uniform(-jitter_ratio, jitter_ratio)
    delay = max(0.0, interval_seconds * (1.0 + jitter))
    return await interruptible_sleep(delay, stop_event)


async def run_gamma_periodic_loop(
    *,
    gamma_worker: GammaMetadataSyncWorker,
    tag_ids: list[int],
    interval_seconds: int,
    backoff_max_seconds: float,
    jitter_ratio: float,
    on_events: Callable[[list[Any]], Awaitable[None]],
    stop_event: asyncio.Event,
    gamma_last_success_at: dict[str, datetime | None],
    emit_log: Callable[[str, dict[str, Any]], None],
) -> None:
    """Repeat ``poll_once`` on ``interval_seconds`` until ``stop_event`` is set.

    HTTP failures use exponential backoff. Failures in ``on_events`` (rules /
    delivery) are **not** treated as HTTP errors and propagate to fail the task;
    they are logged as ``gamma_pipeline_error`` (phase ``on_events``).
    ``gamma_last_success_at`` is updated only after ``on_events`` completes.
    Task cancellation around ``poll_once`` is not logged as fetch failure.
    """
    backoff = 5.0
    while not stop_event.is_set():
        stopped = await sleep_gamma_interval(
            float(interval_seconds),
            jitter_ratio,
            stop_event,
        )
        if stopped:
            return
        try:
            events = await gamma_worker.poll_once(tag_ids=tag_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            emit_log(
                "gamma_poll_error",
                {
                    "phase": "fetch",
                    "error": str(exc),
                    "backoff_sec": backoff,
                },
            )
            stopped = await interruptible_sleep(backoff, stop_event)
            if stopped:
                return
            backoff = min(backoff * 2.0, backoff_max_seconds)
            continue

        try:
            await on_events(events)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            emit_log(
                "gamma_pipeline_error",
                {
                    "phase": "on_events",
                    "error": str(exc),
                },
            )
            raise

        gamma_last_success_at["at"] = datetime.now(timezone.utc)
        backoff = 5.0
