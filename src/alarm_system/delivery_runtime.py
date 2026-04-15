from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from alarm_system.delivery import DeliveryPayload, DeliveryResult, ProviderRegistry
from alarm_system.entities import Alert, ChannelBinding, DeliveryAttempt, DeliveryChannel, DeliveryStatus
from alarm_system.rules.runtime import TriggerDecision
from alarm_system.state import (
    CooldownStore,
    DeliveryIdempotencyStore,
    DeliveryAttemptStore,
    InMemoryDeliveryIdempotencyStore,
    InMemoryCooldownStore,
    InMemoryDeliveryAttemptStore,
    InMemoryTriggerAuditStore,
    TriggerAuditRecord,
    TriggerAuditStore,
)


@dataclass
class DispatchStats:
    queued: int = 0
    sent: int = 0
    failed: int = 0
    skipped_missing_binding: int = 0
    skipped_cooldown: int = 0
    skipped_idempotent: int = 0


@dataclass
class DeliveryDispatcher:
    provider_registry: ProviderRegistry
    cooldown_store: CooldownStore = field(default_factory=InMemoryCooldownStore)
    attempt_store: DeliveryAttemptStore = field(default_factory=InMemoryDeliveryAttemptStore)
    trigger_audit_store: TriggerAuditStore = field(default_factory=InMemoryTriggerAuditStore)
    delivery_idempotency_store: DeliveryIdempotencyStore = field(
        default_factory=InMemoryDeliveryIdempotencyStore
    )
    max_attempts: int = 3
    delivery_idempotency_ttl_seconds: int = 24 * 60 * 60

    async def dispatch(
        self,
        *,
        decision: TriggerDecision,
        alert: Alert,
        bindings: list[ChannelBinding],
    ) -> DispatchStats:
        stats = DispatchStats()
        now = datetime.now(timezone.utc)
        trigger_id = str(uuid5(NAMESPACE_URL, decision.trigger_key))
        self.trigger_audit_store.save_once(
            TriggerAuditRecord(
                trigger_id=trigger_id,
                trigger_key=decision.trigger_key,
                alert_id=decision.alert_id,
                rule_id=decision.rule_id,
                rule_version=decision.rule_version,
                tenant_id=decision.tenant_id,
                scope_id=decision.scope_id,
                reason=decision.reason,
                event_ts=decision.event_ts,
                evaluated_at=decision.reason.evaluated_at,
            )
        )
        for channel in alert.channels:
            binding = _resolve_binding(
                bindings=bindings,
                user_id=alert.user_id,
                channel=channel,
            )
            if binding is None:
                stats.skipped_missing_binding += 1
                continue

            allowed = self.cooldown_store.allow(
                tenant_id=decision.tenant_id,
                rule_id=decision.rule_id,
                rule_version=decision.rule_version,
                scope_id=decision.scope_id,
                channel=channel,
                triggered_at=now,
                cooldown_seconds=alert.cooldown_seconds,
            )
            if not allowed:
                stats.skipped_cooldown += 1
                continue

            idempotency_key = (
                f"{decision.trigger_key}:{channel.value}:{binding.destination}"
            )
            reserved = self.delivery_idempotency_store.reserve(
                idempotency_key,
                ttl_seconds=self.delivery_idempotency_ttl_seconds,
            )
            if not reserved:
                stats.skipped_idempotent += 1
                continue

            payload = DeliveryPayload(
                trigger_id=trigger_id,
                alert_id=alert.alert_id,
                user_id=alert.user_id,
                channel=channel,
                destination=binding.destination,
                subject=alert.alert_type.value,
                body=decision.reason.summary,
                reason_summary=decision.reason.summary,
                metadata={
                    "reason_json": decision.reason.model_dump_json(),
                    "rule_id": decision.rule_id,
                    "rule_version": str(decision.rule_version),
                },
            )
            stats.queued += 1
            result = await self._send_with_retry(payload=payload)
            if result.status is DeliveryStatus.SENT:
                stats.sent += 1
            else:
                stats.failed += 1
        return stats

    async def _send_with_retry(self, payload: DeliveryPayload) -> DeliveryResult:
        provider = self.provider_registry.get(payload.channel)
        last_result: DeliveryResult | None = None
        for attempt_no in range(1, self.max_attempts + 1):
            result = await provider.send(payload)
            last_result = result
            self.attempt_store.save(
                DeliveryAttempt(
                    attempt_id=str(uuid4()),
                    trigger_id=payload.trigger_id,
                    alert_id=payload.alert_id,
                    channel=payload.channel,
                    destination=payload.destination,
                    status=result.status,
                    attempt_no=attempt_no,
                    provider_message_id=result.provider_message_id,
                    error_code=result.error_code,
                    error_detail=result.error_detail,
                    sent_at=datetime.now(timezone.utc)
                    if result.status is DeliveryStatus.SENT
                    else None,
                    next_retry_at=datetime.now(timezone.utc)
                    if result.retryable and attempt_no < self.max_attempts
                    else None,
                )
            )
            if result.status is DeliveryStatus.SENT:
                return result
            if not result.retryable:
                return result
            if attempt_no < self.max_attempts:
                await asyncio.sleep(0)
        return last_result or DeliveryResult(
            status=DeliveryStatus.FAILED,
            error_code="no_result",
            error_detail="provider did not return result",
            retryable=False,
        )


def _resolve_binding(
    *,
    bindings: list[ChannelBinding],
    user_id: str,
    channel: DeliveryChannel,
) -> ChannelBinding | None:
    for binding in bindings:
        if (
            binding.user_id == user_id
            and binding.channel is channel
            and binding.is_verified
        ):
            return binding
    return None
