from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alarm_system.alert_store import PostgresAlertStore
from alarm_system.delivery import DeliveryPayload
from alarm_system.entities import ChannelBinding, DeliveryChannel, DeliveryStatus
from alarm_system.providers import TelegramProvider


@dataclass(frozen=True)
class BroadcastTarget:
    user_id: str
    binding_id: str
    destination: str
    is_verified: bool


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send one test Telegram alert to each current user "
            "(derived from channel bindings)."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send messages. Without this flag command is dry-run.",
    )
    parser.add_argument(
        "--message",
        default="TEST ALERT: manual broadcast check",
        help="Body text for test alert message.",
    )
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="Include unverified Telegram bindings.",
    )
    parser.add_argument(
        "--max-recipients",
        type=int,
        default=0,
        help="Optional cap for recipients (0 means no cap).",
    )
    parser.add_argument(
        "--deduplicate-destination",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Send at most one message per Telegram chat_id "
            "(default: true)."
        ),
    )
    parser.add_argument(
        "--deduplicate-user",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send at most one message per user_id (default: true).",
    )
    return parser.parse_args()


def _parse_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(
        "Invalid boolean value. "
        f"Expected true/false style value, got: {raw!r}"
    )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable {name} is required.")
    return value.strip()


def _load_bindings_from_file(path: str) -> list[ChannelBinding]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(
            f"Expected JSON array in {path}, got {type(payload).__name__}"
        )
    return [ChannelBinding.model_validate(item) for item in payload]


def _load_current_bindings() -> list[ChannelBinding]:
    use_database_config = _parse_bool(
        os.getenv("ALARM_USE_DATABASE_CONFIG"),
        default=False,
    )
    if use_database_config:
        store = PostgresAlertStore(_require_env("ALARM_POSTGRES_DSN"))
        return store.list_bindings(channel=DeliveryChannel.TELEGRAM)
    bindings_path = _require_env("ALARM_CHANNEL_BINDINGS_PATH")
    return _load_bindings_from_file(bindings_path)


def _select_targets(
    bindings: list[ChannelBinding],
    *,
    include_unverified: bool,
    deduplicate_destination: bool,
    deduplicate_user: bool,
    max_recipients: int,
) -> list[BroadcastTarget]:
    targets: list[BroadcastTarget] = []
    seen_destinations: set[str] = set()
    seen_users: set[str] = set()
    for binding in bindings:
        if binding.channel is not DeliveryChannel.TELEGRAM:
            continue
        if not include_unverified and not binding.is_verified:
            continue
        if deduplicate_user and binding.user_id in seen_users:
            continue
        if deduplicate_destination and binding.destination in seen_destinations:
            continue
        targets.append(
            BroadcastTarget(
                user_id=binding.user_id,
                binding_id=binding.binding_id,
                destination=binding.destination,
                is_verified=binding.is_verified,
            )
        )
        if deduplicate_destination:
            seen_destinations.add(binding.destination)
        if deduplicate_user:
            seen_users.add(binding.user_id)
        if max_recipients > 0 and len(targets) >= max_recipients:
            break
    return targets


async def _send_test_alerts(
    *,
    provider: TelegramProvider,
    targets: list[BroadcastTarget],
    message: str,
) -> dict[str, int]:
    stats = {
        "sent": 0,
        "failed": 0,
        "retrying": 0,
        "other": 0,
    }
    for idx, target in enumerate(targets, start=1):
        trigger_id = (
            "manual-test-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-"
            f"{idx}"
        )
        payload = DeliveryPayload(
            trigger_id=trigger_id,
            alert_id="manual-test-alert",
            user_id=target.user_id,
            channel=DeliveryChannel.TELEGRAM,
            destination=target.destination,
            subject="Manual test alert",
            body=message,
            reason_summary="manual_test_broadcast",
            metadata={"binding_id": target.binding_id},
        )
        result = await provider.send(payload)
        if result.status is DeliveryStatus.SENT:
            stats["sent"] += 1
            continue
        if result.status is DeliveryStatus.FAILED:
            stats["failed"] += 1
            continue
        if result.status is DeliveryStatus.RETRYING:
            stats["retrying"] += 1
            continue
        stats["other"] += 1
    return stats


async def _run_async(args: argparse.Namespace) -> None:
    bindings = _load_current_bindings()
    targets = _select_targets(
        bindings,
        include_unverified=args.include_unverified,
        deduplicate_destination=args.deduplicate_destination,
        deduplicate_user=args.deduplicate_user,
        max_recipients=max(0, int(args.max_recipients)),
    )
    dry_run = not args.execute
    if dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "bindings_loaded": len(bindings),
                    "targets_selected": len(targets),
                    "sample_targets": [
                        {
                            "user_id": item.user_id,
                            "binding_id": item.binding_id,
                            "destination": item.destination,
                            "is_verified": item.is_verified,
                        }
                        for item in targets[:10]
                    ],
                },
                ensure_ascii=True,
            )
        )
        return

    provider = TelegramProvider(bot_token=_require_env("ALARM_TELEGRAM_BOT_TOKEN"))
    stats = await _send_test_alerts(
        provider=provider,
        targets=targets,
        message=str(args.message),
    )
    print(
        json.dumps(
            {
                "mode": "execute",
                "bindings_loaded": len(bindings),
                "targets_selected": len(targets),
                **stats,
            },
            ensure_ascii=True,
        )
    )


def main() -> None:
    args = _parse_args()
    asyncio.run(_run_async(args))


if __name__ == "__main__":
    main()
