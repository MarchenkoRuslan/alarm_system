from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from alarm_system.load_harness import (
    LoadHarnessTimeoutError,
    LockedLoadProfile,
    LongBurstLoadProfile,
    run_locked_profile_smoke,
)
from alarm_system.rollback_drill import run_rollback_drill_smoke


def _build_load_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run locked-profile load checks "
            "(smoke or long burst)."
        )
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "long"),
        default="smoke",
        help=(
            "smoke uses compressed 1s windows for CI; "
            "long uses contract 60s burst windows."
        ),
    )
    parser.add_argument(
        "--dispatch-only",
        action="store_true",
        help="Run delivery-only path without rules runtime.",
    )
    parser.add_argument(
        "--target-p95-ms",
        type=float,
        default=1000.0,
        help="SLO threshold for event_to_enqueue_ms p95.",
    )
    parser.add_argument(
        "--max-runtime-sec",
        type=float,
        default=None,
        help="Optional hard runtime budget in seconds.",
    )
    parser.add_argument(
        "--progress-every-events",
        type=int,
        default=0,
        help="Emit progress log every N processed events.",
    )
    return parser


def run_load_gate_main() -> None:
    parser = _build_load_parser()
    args = parser.parse_args()
    profile: LockedLoadProfile
    if args.profile == "long":
        profile = LongBurstLoadProfile(
            target_p95_ms=args.target_p95_ms,
            run_end_to_end=not args.dispatch_only,
            max_runtime_sec=args.max_runtime_sec,
            progress_every_events=args.progress_every_events
            or 2_000,
        )
    else:
        profile = LockedLoadProfile(
            target_p95_ms=args.target_p95_ms,
            run_end_to_end=not args.dispatch_only,
            max_runtime_sec=args.max_runtime_sec,
            progress_every_events=args.progress_every_events,
        )
    try:
        result = asyncio.run(run_locked_profile_smoke(profile))
    except LoadHarnessTimeoutError as exc:
        print(
            json.dumps(
                {"error": "timeout", "detail": str(exc)},
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(json.dumps(asdict(result), ensure_ascii=True))
    if not result.slo.passed:
        raise SystemExit(1)


def run_rollback_gate_main() -> None:
    result = asyncio.run(run_rollback_drill_smoke())
    payload = asdict(result)
    payload["passed"] = result.passed
    print(json.dumps(payload, ensure_ascii=True))
    if not result.passed:
        raise SystemExit(1)
