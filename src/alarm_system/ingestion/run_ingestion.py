from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

from alarm_system.canonical_event import CanonicalEvent
from alarm_system.ingestion.metrics import InMemoryMetrics
from alarm_system.ingestion.polymarket.adapter import PolymarketMarketAdapter
from alarm_system.ingestion.polymarket.gamma_sync import (
    GammaMetadataSyncWorker,
)
from alarm_system.ingestion.polymarket.supervisor import (
    PolymarketIngestionSupervisor,
    SupervisorConfig,
)
from alarm_system.ingestion.polymarket.ws_client import PolymarketWsClient


@dataclass(frozen=True)
class IngestionRuntimeConfig:
    asset_ids: list[str]
    gamma_tag_ids: list[int]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Polymarket ingestion runtime loop."
    )
    parser.add_argument(
        "--asset-id",
        dest="asset_ids",
        action="append",
        required=True,
        help="Polymarket asset id for WS market subscription (repeatable).",
    )
    parser.add_argument(
        "--gamma-tag-id",
        dest="gamma_tag_ids",
        action="append",
        type=int,
        default=[],
        help="Gamma tag_id for metadata sync (repeatable).",
    )
    return parser.parse_args()


async def run(runtime_config: IngestionRuntimeConfig) -> None:
    """Ingestion CLI: one Gamma ``poll_once`` at startup when tags are set.

    Periodic Gamma polling is implemented in ``service_runtime.run`` (worker)
    via ``ALARM_GAMMA_POLL_INTERVAL_SECONDS``; this debug CLI does not run
    a background Gamma loop.
    """
    metrics = InMemoryMetrics()
    adapter = PolymarketMarketAdapter(metrics=metrics)
    ws_client = PolymarketWsClient()
    supervisor = PolymarketIngestionSupervisor(
        ws_client=ws_client,
        adapter=adapter,
        config=SupervisorConfig(asset_ids=runtime_config.asset_ids),
        metrics=metrics,
    )
    gamma_worker = GammaMetadataSyncWorker(metrics=metrics)

    async def on_events(events: list[CanonicalEvent]) -> None:
        for event in events:
            print(json.dumps(event.model_dump(mode="json"), ensure_ascii=True))

    stop_event = asyncio.Event()
    supervisor_task = asyncio.create_task(
        supervisor.run(on_events=on_events, stop_event=stop_event)
    )

    try:
        if runtime_config.gamma_tag_ids:
            metadata_events = await gamma_worker.poll_once(
                tag_ids=runtime_config.gamma_tag_ids
            )
            await on_events(metadata_events)
        await asyncio.shield(supervisor_task)
    except asyncio.CancelledError:
        stop_event.set()
        try:
            await asyncio.wait_for(supervisor_task, timeout=2.0)
        except asyncio.TimeoutError:
            supervisor_task.cancel()
            await asyncio.gather(supervisor_task, return_exceptions=True)
        raise
    finally:
        stop_event.set()
        if not supervisor_task.done():
            try:
                await asyncio.wait_for(supervisor_task, timeout=2.0)
            except asyncio.TimeoutError:
                supervisor_task.cancel()
                await asyncio.gather(supervisor_task, return_exceptions=True)
        await ws_client.close()
        print(
            json.dumps({"metrics": asdict(metrics.snapshot())}, ensure_ascii=True)
        )


def main() -> None:
    args = _parse_args()
    config = IngestionRuntimeConfig(
        asset_ids=args.asset_ids,
        gamma_tag_ids=args.gamma_tag_ids or [],
    )
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        # SIGINT usually arrives as CancelledError inside run().
        pass


if __name__ == "__main__":
    main()
