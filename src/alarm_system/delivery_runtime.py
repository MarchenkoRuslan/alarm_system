from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

logger = logging.getLogger(__name__)

from alarm_system.backpressure import BackpressureController
from alarm_system.delivery import (
    DeliveryPayload,
    DeliveryResult,
    ProviderRegistry,
)
from alarm_system.entities import (
    Alert,
    ChannelBinding,
    DeliveryAttempt,
    DeliveryChannel,
    DeliveryStatus,
)
from alarm_system.observability import RuntimeObservability
from alarm_system.rules.runtime import TriggerDecision
from alarm_system.state import (
    CooldownStore,
    DeliveryIdempotencyStore,
    DeliveryAttemptStore,
    InMemoryDeliveryIdempotencyStore,
    InMemoryCooldownStore,
    InMemoryDeliveryAttemptStore,
    InMemoryMuteStore,
    InMemoryTriggerAuditStore,
    MuteStore,
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
    skipped_backpressure: int = 0
    skipped_muted: int = 0


@dataclass(frozen=True)
class EnqueuedDelivery:
    payload: DeliveryPayload
    enqueued_at: datetime


@dataclass
class DeliveryDispatcher:
    provider_registry: ProviderRegistry
    cooldown_store: CooldownStore = field(
        default_factory=InMemoryCooldownStore
    )
    attempt_store: DeliveryAttemptStore = field(
        default_factory=InMemoryDeliveryAttemptStore
    )
    trigger_audit_store: TriggerAuditStore = field(
        default_factory=InMemoryTriggerAuditStore
    )
    delivery_idempotency_store: DeliveryIdempotencyStore = field(
        default_factory=InMemoryDeliveryIdempotencyStore
    )
    mute_store: MuteStore = field(default_factory=InMemoryMuteStore)
    max_attempts: int = 3
    delivery_idempotency_ttl_seconds: int = 24 * 60 * 60
    observability: RuntimeObservability | None = None
    backpressure: BackpressureController | None = None

    async def dispatch(
        self,
        *,
        decision: TriggerDecision,
        alert: Alert,
        bindings: list[ChannelBinding],
        execute_sends: bool = True,
    ) -> DispatchStats:
        stats = DispatchStats()
        if self._is_user_muted(alert):
            stats.skipped_muted += len(alert.channels)
            self._observe_mute_skip(alert)
            return stats
        trigger_id = self._audit_trigger(decision)
        enqueued_items = self._build_enqueued_items(
            decision=decision,
            alert=alert,
            bindings=bindings,
            trigger_id=trigger_id,
            stats=stats,
        )
        if not execute_sends:
            self._release_backpressure_for_items(enqueued_items)
            return stats
        await self._deliver_enqueued_items(
            enqueued_items,
            stats,
            user_id=alert.user_id,
        )
        return stats

    def _is_user_muted(self, alert: Alert) -> bool:
        try:
            return self.mute_store.get_mute_until(alert.user_id) is not None
        except Exception as exc:  # noqa: BLE001
            # Mute store failures must never block delivery. We
            # fail-open but make the failure observable so ops sees a
            # silent Redis outage instead of silently sending alerts
            # during a claimed mute.
            logger.warning(
                "mute_store_check_failed",
                extra={"error": str(exc)},
            )
            if self.observability is not None:
                self.observability.increment(
                    "delivery_mute_check_failed_total",
                )
            return False

    def _observe_mute_skip(self, alert: Alert) -> None:
        if self.observability is None:
            return
        for channel in alert.channels:
            self.observability.increment(
                "delivery_skipped_muted_total",
                labels={"channel": channel.value},
            )

    def _audit_trigger(self, decision: TriggerDecision) -> str:
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
        return trigger_id

    def _build_enqueued_items(
        self,
        *,
        decision: TriggerDecision,
        alert: Alert,
        bindings: list[ChannelBinding],
        trigger_id: str,
        stats: DispatchStats,
    ) -> list[EnqueuedDelivery]:
        enqueued_items: list[EnqueuedDelivery] = []
        for channel in alert.channels:
            prepared = self._prepare_channel_enqueue(
                decision=decision,
                alert=alert,
                bindings=bindings,
                channel=channel,
                trigger_id=trigger_id,
                stats=stats,
            )
            if prepared is not None:
                enqueued_items.append(prepared)
        return enqueued_items

    def _prepare_channel_enqueue(
        self,
        *,
        decision: TriggerDecision,
        alert: Alert,
        bindings: list[ChannelBinding],
        channel: DeliveryChannel,
        trigger_id: str,
        stats: DispatchStats,
    ) -> EnqueuedDelivery | None:
        now = datetime.now(timezone.utc)
        binding = _resolve_binding(
            bindings=bindings,
            user_id=alert.user_id,
            channel=channel,
        )
        if binding is None:
            stats.skipped_missing_binding += 1
            return None
        if not self._passes_cooldown(
            decision=decision,
            alert=alert,
            channel=channel,
            now=now,
        ):
            stats.skipped_cooldown += 1
            return None
        if not self._reserve_backpressure(channel, stats):
            return None
        if not self._reserve_idempotency(
            decision=decision,
            channel=channel,
            destination=binding.destination,
        ):
            stats.skipped_idempotent += 1
            self._release_backpressure_slot()
            return None
        payload = self._build_payload(
            trigger_id=trigger_id,
            decision=decision,
            alert=alert,
            channel=channel,
            destination=binding.destination,
        )
        enqueued_at = datetime.now(timezone.utc)
        self._observe_enqueue_latency(
            event_ts=decision.event_ts,
            enqueued_at=enqueued_at,
            channel=channel,
            decision=decision,
        )
        stats.queued += 1
        return EnqueuedDelivery(payload=payload, enqueued_at=enqueued_at)

    def _passes_cooldown(
        self,
        *,
        decision: TriggerDecision,
        alert: Alert,
        channel: DeliveryChannel,
        now: datetime,
    ) -> bool:
        return self.cooldown_store.allow(
            tenant_id=decision.tenant_id,
            rule_id=decision.rule_id,
            rule_version=decision.rule_version,
            scope_id=decision.scope_id,
            channel=channel,
            triggered_at=now,
            cooldown_seconds=alert.cooldown_seconds,
        )

    def _reserve_backpressure(
        self, channel: DeliveryChannel, stats: DispatchStats
    ) -> bool:
        if self.backpressure is None:
            return True
        accepted = self.backpressure.reserve_slot()
        self._observe_backpressure_state()
        if accepted:
            return True
        stats.skipped_backpressure += 1
        if self.observability is not None:
            self.observability.increment(
                "backpressure_rejected_total",
                labels={"channel": channel.value},
            )
        return False

    def _reserve_idempotency(
        self,
        *,
        decision: TriggerDecision,
        channel: DeliveryChannel,
        destination: str,
    ) -> bool:
        idempotency_key = (
            f"{decision.trigger_key}:{channel.value}:{destination}"
        )
        return self.delivery_idempotency_store.reserve(
            idempotency_key,
            ttl_seconds=self.delivery_idempotency_ttl_seconds,
        )

    @staticmethod
    def _build_payload(
        *,
        trigger_id: str,
        decision: TriggerDecision,
        alert: Alert,
        channel: DeliveryChannel,
        destination: str,
    ) -> DeliveryPayload:
        return DeliveryPayload(
            trigger_id=trigger_id,
            alert_id=alert.alert_id,
            user_id=alert.user_id,
            channel=channel,
            destination=destination,
            subject=alert.alert_type.value,
            body=decision.reason.summary,
            reason_summary=decision.reason.summary,
            metadata={
                "reason_json": decision.reason.model_dump_json(),
                "rule_id": decision.rule_id,
                "rule_version": str(decision.rule_version),
            },
        )

    def _release_backpressure_for_items(
        self, items: list[EnqueuedDelivery]
    ) -> None:
        for _ in items:
            self._release_backpressure_slot()

    def _release_backpressure_slot(self) -> None:
        if self.backpressure is None:
            return
        self.backpressure.release_slot()
        self._observe_backpressure_state()

    async def _deliver_enqueued_items(
        self,
        items: list[EnqueuedDelivery],
        stats: DispatchStats,
        *,
        user_id: str,
    ) -> None:
        for item in items:
            self._observe_queue_lag(
                channel=item.payload.channel,
                enqueued_at=item.enqueued_at,
                dequeued_at=datetime.now(timezone.utc),
            )
            try:
                result = await self._send_with_retry(
                    payload=item.payload,
                    enqueued_at=item.enqueued_at,
                    user_id=user_id,
                )
            finally:
                self._release_backpressure_for_items([item])
            if result.status is DeliveryStatus.SENT:
                stats.sent += 1
            else:
                stats.failed += 1

    def _observe_enqueue_latency(
        self,
        *,
        event_ts: datetime,
        enqueued_at: datetime,
        channel: DeliveryChannel,
        decision: TriggerDecision,
    ) -> None:
        if self.observability is None:
            return
        delta_ms = max(
            0.0,
            (
                enqueued_at - event_ts.astimezone(timezone.utc)
            ).total_seconds()
            * 1000.0,
        )
        self.observability.observe_timing_ms(
            "event_to_enqueue_ms",
            delta_ms,
            labels={
                "scenario": decision.scenario or "custom",
                "rule_type": decision.rule_type or "unknown",
                "channel": channel.value,
                "source": decision.source or "unknown",
                "event_type": decision.event_type or "unknown",
            },
        )

    def _observe_queue_lag(
        self,
        *,
        channel: DeliveryChannel,
        enqueued_at: datetime,
        dequeued_at: datetime,
    ) -> None:
        if self.observability is None:
            return
        lag_ms = max(
            0.0,
            (
                dequeued_at - enqueued_at.astimezone(timezone.utc)
            ).total_seconds()
            * 1000.0,
        )
        self.observability.observe_timing_ms(
            "queue_lag_ms",
            lag_ms,
            labels={
                "queue_name": "delivery_main",
                "channel": channel.value,
            },
        )

    def _observe_backpressure_state(self) -> None:
        if self.backpressure is None or self.observability is None:
            return
        snapshot = self.backpressure.snapshot()
        self.observability.observe_timing_ms(
            "queue_utilization_pct",
            snapshot.utilization * 100.0,
            labels={"state": snapshot.state},
        )
        if snapshot.degrade_non_critical:
            self.observability.increment("backpressure_critical_total")

    async def _send_with_retry(
        self,
        *,
        payload: DeliveryPayload,
        enqueued_at: datetime,
        user_id: str,
    ) -> DeliveryResult:
        provider = self.provider_registry.get(payload.channel)
        last_result: DeliveryResult | None = None
        for attempt_no in range(1, self.max_attempts + 1):
            result = await provider.send(payload)
            last_result = result
            attempt_status = _attempt_status(
                result=result,
                attempt_no=attempt_no,
                max_attempts=self.max_attempts,
            )
            record = DeliveryAttempt(
                attempt_id=str(uuid4()),
                trigger_id=payload.trigger_id,
                alert_id=payload.alert_id,
                channel=payload.channel,
                destination=payload.destination,
                status=attempt_status,
                attempt_no=attempt_no,
                provider_message_id=result.provider_message_id,
                error_code=result.error_code,
                error_detail=result.error_detail,
                enqueued_at=enqueued_at,
                sent_at=datetime.now(timezone.utc)
                if result.status is DeliveryStatus.SENT
                else None,
                next_retry_at=datetime.now(timezone.utc)
                if attempt_status is DeliveryStatus.RETRYING
                else None,
            )
            save_for_user = getattr(self.attempt_store, "save_for_user", None)
            if callable(save_for_user):
                save_for_user(record, user_id=user_id)
            else:
                self.attempt_store.save(record)
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


def _attempt_status(
    *,
    result: DeliveryResult,
    attempt_no: int,
    max_attempts: int,
) -> DeliveryStatus:
    if result.status is DeliveryStatus.SENT:
        return DeliveryStatus.SENT
    if result.retryable and attempt_no < max_attempts:
        return DeliveryStatus.RETRYING
    return DeliveryStatus.FAILED
