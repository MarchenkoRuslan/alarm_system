from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from alarm_system.rules_dsl import build_trigger_key


@dataclass(frozen=True)
class DedupInput:
    tenant_id: str
    rule_id: str
    rule_version: int
    scope_id: str
    bucket_seconds: int
    event_time: datetime


def dedup_key(data: DedupInput) -> str:
    event_time = data.event_time
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    return build_trigger_key(
        tenant_id=data.tenant_id,
        rule_id=data.rule_id,
        rule_version=data.rule_version,
        scope_id=data.scope_id,
        bucket_seconds=data.bucket_seconds,
        at=event_time,
    )


def cooldown_key(
    tenant_id: str, rule_id: str, rule_version: int, scope_id: str, channel: str,
) -> str:
    return f"cooldown:{tenant_id}:{rule_id}:{rule_version}:{scope_id}:{channel}"


def deferred_watch_key(alert_id: str, market_id: str) -> str:
    return f"deferred_watch:{alert_id}:{market_id}"
