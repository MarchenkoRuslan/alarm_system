from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from alarm_system.backpressure import BackpressureController
from alarm_system.alert_store import (
    AlertStoreBackendError,
    build_cached_alert_store,
)
from alarm_system.compute.prefilter import RuleBinding
from alarm_system.delivery import ProviderRegistry
from alarm_system.delivery_runtime import DeliveryDispatcher, DispatchStats
from alarm_system.entities import Alert, ChannelBinding
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
from alarm_system.observability import RuntimeObservability
from alarm_system.providers import TelegramProvider
from alarm_system.rules import (
    RedisBackedDeferredWatchStore,
    RedisSuppressionStore,
    RuleRuntime,
)
from alarm_system.rules_dsl import AlertRuleV1
from alarm_system.state import (
    RedisCooldownStore,
    RedisDeliveryAttemptStore,
    RedisDeliveryIdempotencyStore,
    RedisDeferredWatchStore,
    RedisMuteStore,
    RedisSuppressionWindowStateStore,
    RedisTriggerAuditStore,
    RedisTriggerDedupStore,
)

logger = logging.getLogger(__name__)


class ServiceRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[str] = Field(min_length=1)
    gamma_tag_ids: list[int] = Field(default_factory=list)
    rules_path: str
    alerts_path: str
    channel_bindings_path: str
    redis_url: str
    use_database_config: bool = False
    postgres_dsn: str | None = None
    config_cache_ttl_seconds: int = Field(default=30, ge=1)
    telegram_bot_token: str | None = None
    execute_sends: bool = True
    dedup_bucket_seconds: int = Field(default=60, ge=1)
    dedup_safety_margin_seconds: int = Field(default=5, ge=0)
    dispatch_max_attempts: int = Field(default=3, ge=1)
    delivery_idempotency_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)
    backpressure_capacity: int = Field(default=10_000, ge=1)
    backpressure_warning_utilization: float = Field(
        default=0.70, gt=0.0, lt=1.0
    )
    backpressure_critical_utilization: float = Field(
        default=0.90, gt=0.0, le=1.0
    )
    backpressure_recovery_samples: int = Field(default=3, ge=1)
    progress_every_events: int = Field(default=500, ge=0)
    metrics_every_seconds: int = Field(default=30, ge=1)
    gamma_poll_interval_seconds: int = Field(
        default=0,
        ge=0,
        description=(
            "Background Gamma HTTP poll interval; 0 disables periodic polls "
            "(bootstrap poll_once at startup still runs when gamma_tag_ids set)."
        ),
    )
    gamma_poll_backoff_max_seconds: float = Field(
        default=300.0,
        ge=1.0,
        description="Max exponential backoff after a failed Gamma poll.",
    )
    gamma_poll_jitter_ratio: float = Field(
        default=0.1,
        ge=0.0,
        le=0.5,
        description="Random jitter fraction applied to each Gamma poll sleep.",
    )

    @model_validator(mode="after")
    def _validate_send_requirements(self) -> "ServiceRuntimeConfig":
        if self.execute_sends and not self.telegram_bot_token:
            raise ValueError(
                "telegram_bot_token is required when execute_sends=true"
            )
        if (
            self.backpressure_warning_utilization
            >= self.backpressure_critical_utilization
        ):
            raise ValueError(
                "backpressure_warning_utilization must be < "
                "backpressure_critical_utilization"
            )
        if self.use_database_config and not self.postgres_dsn:
            raise ValueError(
                "postgres_dsn is required when use_database_config=true"
            )
        if self.gamma_poll_interval_seconds > 0 and not self.gamma_tag_ids:
            raise ValueError(
                "gamma_poll_interval_seconds > 0 requires non-empty "
                "gamma_tag_ids (set ALARM_GAMMA_TAG_IDS)"
            )
        return self

    @classmethod
    def from_env(
        cls,
        *,
        execute_sends_override: bool | None = None,
    ) -> "ServiceRuntimeConfig":
        payload: dict[str, Any] = {
            "asset_ids": _parse_csv(os.getenv("ALARM_ASSET_IDS")),
            "gamma_tag_ids": _parse_int_csv(
                os.getenv("ALARM_GAMMA_TAG_IDS")
            ),
            "rules_path": _require_env("ALARM_RULES_PATH"),
            "alerts_path": _require_env("ALARM_ALERTS_PATH"),
            "channel_bindings_path": _require_env(
                "ALARM_CHANNEL_BINDINGS_PATH"
            ),
            "redis_url": _require_env("ALARM_REDIS_URL"),
            "use_database_config": _parse_bool(
                os.getenv("ALARM_USE_DATABASE_CONFIG"), default=False
            ),
            "postgres_dsn": os.getenv("ALARM_POSTGRES_DSN"),
            "config_cache_ttl_seconds": _parse_int_env(
                "ALARM_CONFIG_CACHE_TTL_SECONDS",
                default=30,
            ),
            "telegram_bot_token": os.getenv("ALARM_TELEGRAM_BOT_TOKEN"),
            "execute_sends": _parse_bool(
                os.getenv("ALARM_EXECUTE_SENDS"), default=True
            ),
            "dedup_bucket_seconds": _parse_int_env(
                "ALARM_DEDUP_BUCKET_SECONDS", default=60
            ),
            "dedup_safety_margin_seconds": _parse_int_env(
                "ALARM_DEDUP_SAFETY_MARGIN_SECONDS", default=5
            ),
            "dispatch_max_attempts": _parse_int_env(
                "ALARM_DISPATCH_MAX_ATTEMPTS", default=3
            ),
            "delivery_idempotency_ttl_seconds": _parse_int_env(
                "ALARM_DELIVERY_IDEMPOTENCY_TTL_SECONDS",
                default=24 * 60 * 60,
            ),
            "backpressure_capacity": _parse_int_env(
                "ALARM_BACKPRESSURE_CAPACITY",
                default=10_000,
            ),
            "backpressure_warning_utilization": _parse_float_env(
                "ALARM_BACKPRESSURE_WARNING_UTILIZATION",
                default=0.70,
            ),
            "backpressure_critical_utilization": _parse_float_env(
                "ALARM_BACKPRESSURE_CRITICAL_UTILIZATION",
                default=0.90,
            ),
            "backpressure_recovery_samples": _parse_int_env(
                "ALARM_BACKPRESSURE_RECOVERY_SAMPLES",
                default=3,
            ),
            "progress_every_events": _parse_int_env(
                "ALARM_PROGRESS_EVERY_EVENTS",
                default=500,
            ),
            "metrics_every_seconds": _parse_int_env(
                "ALARM_METRICS_EVERY_SECONDS",
                default=30,
            ),
            "gamma_poll_interval_seconds": _parse_int_env(
                "ALARM_GAMMA_POLL_INTERVAL_SECONDS",
                default=0,
            ),
            "gamma_poll_backoff_max_seconds": _parse_float_env(
                "ALARM_GAMMA_POLL_BACKOFF_MAX_SECONDS",
                default=300.0,
            ),
            "gamma_poll_jitter_ratio": _parse_float_env(
                "ALARM_GAMMA_POLL_JITTER_RATIO",
                default=0.1,
            ),
        }
        if execute_sends_override is not None:
            payload["execute_sends"] = execute_sends_override
        return cls.model_validate(payload)


@dataclass
class RuntimeCounters:
    events_seen: int = 0
    decisions_emitted: int = 0
    delivery_queued: int = 0
    delivery_sent: int = 0
    delivery_failed: int = 0
    skipped_missing_binding: int = 0
    skipped_cooldown: int = 0
    skipped_idempotent: int = 0
    skipped_backpressure: int = 0
    skipped_muted: int = 0

    def apply_dispatch_stats(self, stats: DispatchStats) -> None:
        self.delivery_queued += stats.queued
        self.delivery_sent += stats.sent
        self.delivery_failed += stats.failed
        self.skipped_missing_binding += stats.skipped_missing_binding
        self.skipped_cooldown += stats.skipped_cooldown
        self.skipped_idempotent += stats.skipped_idempotent
        self.skipped_backpressure += stats.skipped_backpressure
        self.skipped_muted += stats.skipped_muted


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run full production pipeline "
            "(ingestion -> rules -> delivery) for Polymarket MVP."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and enqueue path only; provider sends are disabled.",
    )
    return parser.parse_args()


def _build_config(args: argparse.Namespace) -> ServiceRuntimeConfig:
    if args.dry_run:
        return ServiceRuntimeConfig.from_env(execute_sends_override=False)
    return ServiceRuntimeConfig.from_env()


def _load_json_list(path: str) -> list[dict[str, Any]]:
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(content, list):
        raise ValueError(f"{path} must contain a JSON array")
    records: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            raise ValueError(f"{path} must contain array of JSON objects")
        records.append(item)
    return records


def _load_rules(path: str) -> list[AlertRuleV1]:
    return [AlertRuleV1.model_validate(item) for item in _load_json_list(path)]


def _load_alerts(path: str) -> list[Alert]:
    alerts = [Alert.model_validate(item) for item in _load_json_list(path)]
    return [alert for alert in alerts if alert.enabled]


def _load_channel_bindings(path: str) -> list[ChannelBinding]:
    return [
        ChannelBinding.model_validate(item)
        for item in _load_json_list(path)
    ]


def _load_runtime_alert_config(
    config: ServiceRuntimeConfig,
    *,
    redis_client: Any,
) -> tuple[list[Alert], list[ChannelBinding]]:
    if not config.use_database_config:
        return (
            _load_alerts(config.alerts_path),
            _load_channel_bindings(config.channel_bindings_path),
        )
    try:
        cached_store = build_cached_alert_store(
            postgres_dsn=str(config.postgres_dsn),
            redis_client=redis_client,
            cache_ttl_seconds=config.config_cache_ttl_seconds,
        )
        return cached_store.get_runtime_snapshot()
    except AlertStoreBackendError as exc:
        raise RuntimeError(
            "Failed to load alert runtime config from Postgres/Redis cache: "
            f"{exc}"
        ) from exc


def _build_rule_bindings(
    rules: list[AlertRuleV1],
    alerts: list[Alert],
) -> tuple[list[RuleBinding], dict[str, Alert]]:
    rule_by_identity: dict[tuple[str, int], AlertRuleV1] = {}
    for rule in rules:
        identity = (rule.rule_id, rule.version)
        if identity in rule_by_identity:
            raise ValueError(
                "Duplicate rule identity in rules file: "
                f"{identity[0]}#{identity[1]}"
            )
        rule_by_identity[identity] = rule

    bindings: list[RuleBinding] = []
    alert_by_id: dict[str, Alert] = {}
    unknown_rule_identities: list[str] = []
    for alert in alerts:
        alert_by_id[alert.alert_id] = alert
        identity = (alert.rule_id, alert.rule_version)
        rule = rule_by_identity.get(identity)
        if rule is None:
            unknown_rule_identities.append(
                f"{alert.alert_id} -> {identity[0]}#{identity[1]}"
            )
            continue
        bindings.append(RuleBinding(alert_id=alert.alert_id, rule=rule))
    if unknown_rule_identities:
        sample = ", ".join(unknown_rule_identities[:3])
        raise ValueError(
            "Alert references unknown rule identity. "
            f"count={len(unknown_rule_identities)} "
            f"examples=[{sample}]"
        )
    return bindings, alert_by_id


def _build_redis_client(redis_url: str) -> Any:
    try:
        import redis
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'redis' package is required for production runtime. "
            "Install dependencies with pip install -e \".[ingestion,dev]\"."
        ) from exc
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _safe_redis_url(redis_url: str) -> str:
    try:
        parsed = urlsplit(redis_url)
    except ValueError:
        return "<invalid_redis_url>"
    if parsed.password is None:
        return redis_url
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _verify_redis_connectivity(redis_client: Any, redis_url: str) -> None:
    try:
        ok = redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Redis startup check failed for "
            f"{_safe_redis_url(redis_url)}: {exc}"
        ) from exc
    if ok is not True:
        raise RuntimeError(
            "Redis startup check failed for "
            f"{_safe_redis_url(redis_url)}: unexpected ping response"
        )


def _emit_json_log(kind: str, payload: dict[str, Any]) -> None:
    envelope = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **payload,
    }
    print(json.dumps(envelope, ensure_ascii=True), flush=True)


def _build_runtime(
    config: ServiceRuntimeConfig,
    *,
    ingest_metrics: InMemoryMetrics,
    observability: RuntimeObservability,
) -> tuple[
    RuleRuntime,
    DeliveryDispatcher,
    PolymarketIngestionSupervisor,
    GammaMetadataSyncWorker,
    PolymarketWsClient,
    dict[str, Alert],
    list[ChannelBinding],
]:
    redis_client = _build_redis_client(config.redis_url)
    _verify_redis_connectivity(redis_client, config.redis_url)
    rules = _load_rules(config.rules_path)
    alerts, channel_bindings = _load_runtime_alert_config(
        config,
        redis_client=redis_client,
    )
    rule_bindings, alert_by_id = _build_rule_bindings(rules, alerts)
    runtime = RuleRuntime(
        deferred_watches=RedisBackedDeferredWatchStore(
            RedisDeferredWatchStore(redis_client)
        ),
        suppression=RedisSuppressionStore(
            RedisSuppressionWindowStateStore(redis_client)
        ),
        dedup=RedisTriggerDedupStore(redis_client),
        dedup_bucket_seconds=config.dedup_bucket_seconds,
        dedup_safety_margin_seconds=config.dedup_safety_margin_seconds,
        observability=observability,
    )
    runtime.set_bindings(rule_bindings)

    provider_registry = ProviderRegistry()
    if config.execute_sends:
        provider_registry.register(
            TelegramProvider(bot_token=str(config.telegram_bot_token))
        )
    dispatcher = DeliveryDispatcher(
        provider_registry=provider_registry,
        cooldown_store=RedisCooldownStore(redis_client),
        attempt_store=RedisDeliveryAttemptStore(redis_client),
        trigger_audit_store=RedisTriggerAuditStore(redis_client),
        delivery_idempotency_store=RedisDeliveryIdempotencyStore(redis_client),
        mute_store=RedisMuteStore(redis_client),
        max_attempts=config.dispatch_max_attempts,
        delivery_idempotency_ttl_seconds=(
            config.delivery_idempotency_ttl_seconds
        ),
        observability=observability,
        backpressure=BackpressureController(
            capacity=config.backpressure_capacity,
            warning_utilization=config.backpressure_warning_utilization,
            critical_utilization=config.backpressure_critical_utilization,
            recovery_window_samples=config.backpressure_recovery_samples,
        ),
    )

    ws_client = PolymarketWsClient()
    supervisor = PolymarketIngestionSupervisor(
        ws_client=ws_client,
        adapter=PolymarketMarketAdapter(metrics=ingest_metrics),
        config=SupervisorConfig(asset_ids=config.asset_ids),
        metrics=ingest_metrics,
    )
    gamma_worker = GammaMetadataSyncWorker(metrics=ingest_metrics)
    return (
        runtime,
        dispatcher,
        supervisor,
        gamma_worker,
        ws_client,
        alert_by_id,
        channel_bindings,
    )


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> bool:
    """Return True if ``stop_event`` was set during the sleep."""
    if seconds <= 0:
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _sleep_gamma_interval(
    interval_seconds: float,
    jitter_ratio: float,
    stop_event: asyncio.Event,
) -> bool:
    """Sleep one Gamma poll interval with jitter. Returns True if stopped."""
    jitter = random.uniform(-jitter_ratio, jitter_ratio)
    delay = max(0.0, interval_seconds * (1.0 + jitter))
    return await _interruptible_sleep(delay, stop_event)


async def _run_gamma_periodic_loop(
    *,
    gamma_worker: GammaMetadataSyncWorker,
    tag_ids: list[int],
    interval_seconds: int,
    backoff_max_seconds: float,
    jitter_ratio: float,
    on_events: Callable[[list[Any]], Awaitable[None]],
    stop_event: asyncio.Event,
    gamma_last_success_at: dict[str, datetime | None],
) -> None:
    """Repeat ``poll_once`` on ``interval_seconds`` until ``stop_event`` is set.

    HTTP failures use exponential backoff. Failures in ``on_events`` (rules /
    delivery) are **not** treated as HTTP errors and propagate to fail the task.
    """
    backoff = 5.0
    while not stop_event.is_set():
        stopped = await _sleep_gamma_interval(
            float(interval_seconds),
            jitter_ratio,
            stop_event,
        )
        if stopped:
            return
        try:
            events = await gamma_worker.poll_once(tag_ids=tag_ids)
        except Exception as exc:  # noqa: BLE001
            _emit_json_log(
                "gamma_poll_error",
                {
                    "phase": "fetch",
                    "error": str(exc),
                    "backoff_sec": backoff,
                },
            )
            stopped = await _interruptible_sleep(backoff, stop_event)
            if stopped:
                return
            backoff = min(backoff * 2.0, backoff_max_seconds)
            continue

        gamma_last_success_at["at"] = datetime.now(timezone.utc)
        await on_events(events)
        backoff = 5.0


def _emit_startup_logs(
    *,
    config: ServiceRuntimeConfig,
    alert_by_id: dict[str, Alert],
    channel_bindings: list[ChannelBinding],
) -> None:
    _emit_json_log(
        "startup_checks",
        {
            "redis_connectivity": "ok",
            "redis_url": _safe_redis_url(config.redis_url),
            "mode": "dry_run" if not config.execute_sends else "live",
            "config_source": (
                "postgres+redis_cache"
                if config.use_database_config
                else "json_files"
            ),
        },
    )
    _emit_json_log(
        "startup",
        {
            "mode": "dry_run" if not config.execute_sends else "live",
            "asset_ids": config.asset_ids,
            "gamma_tag_ids": config.gamma_tag_ids,
            "gamma_poll_interval_seconds": config.gamma_poll_interval_seconds,
            "alerts_loaded": len(alert_by_id),
            "bindings_loaded": len(channel_bindings),
        },
    )


async def _shutdown_supervisor(
    *,
    stop_event: asyncio.Event,
    supervisor_task: asyncio.Task[None],
    ws_client: PolymarketWsClient,
) -> None:
    stop_event.set()
    if not supervisor_task.done():
        try:
            await asyncio.wait_for(supervisor_task, timeout=2.0)
        except asyncio.TimeoutError:
            supervisor_task.cancel()
            await asyncio.gather(supervisor_task, return_exceptions=True)
    await ws_client.close()


async def run(config: ServiceRuntimeConfig) -> None:
    ingest_metrics = InMemoryMetrics()
    observability = RuntimeObservability()
    counters = RuntimeCounters()
    (
        runtime,
        dispatcher,
        supervisor,
        gamma_worker,
        ws_client,
        alert_by_id,
        channel_bindings,
    ) = _build_runtime(
        config,
        ingest_metrics=ingest_metrics,
        observability=observability,
    )

    progress_started_at = datetime.now(timezone.utc)
    last_metrics_emit = datetime.now(timezone.utc)
    gamma_last_success_at: dict[str, datetime | None] = {"at": None}
    event_pipeline_lock = asyncio.Lock()

    async def on_events(events: list[Any]) -> None:
        """Serialize WS and Gamma batches: shared RuleRuntime and counters."""
        nonlocal last_metrics_emit
        async with event_pipeline_lock:
            for event in events:
                counters.events_seen += 1
                decisions = runtime.evaluate_event(event)
                counters.decisions_emitted += len(decisions)
                for decision in decisions:
                    alert = alert_by_id.get(decision.alert_id)
                    if alert is None:
                        continue
                    stats = await dispatcher.dispatch(
                        decision=decision,
                        alert=alert,
                        bindings=channel_bindings,
                        execute_sends=config.execute_sends,
                    )
                    counters.apply_dispatch_stats(stats)

                if (
                    config.progress_every_events > 0
                    and counters.events_seen % config.progress_every_events == 0
                ):
                    elapsed = (
                        datetime.now(timezone.utc) - progress_started_at
                    ).total_seconds()
                    _emit_json_log(
                        "progress",
                        {
                            "events_seen": counters.events_seen,
                            "decisions_emitted": counters.decisions_emitted,
                            "delivery_queued": counters.delivery_queued,
                            "delivery_sent": counters.delivery_sent,
                            "skipped_muted": counters.skipped_muted,
                            "elapsed_sec": elapsed,
                        },
                    )

                now = datetime.now(timezone.utc)
                if (
                    (now - last_metrics_emit).total_seconds()
                    >= config.metrics_every_seconds
                ):
                    last_metrics_emit = now
                    success_at = gamma_last_success_at.get("at")
                    if success_at is not None:
                        ingest_metrics.set_gauge(
                            "ingestion.gamma.last_success_age_sec",
                            (now - success_at).total_seconds(),
                        )
                    _emit_json_log(
                        "metrics_snapshot",
                        {
                            "runtime": observability.snapshot(),
                            "ingestion": ingest_metrics.snapshot().__dict__,
                        },
                    )

    stop_event = asyncio.Event()
    supervisor_task = asyncio.create_task(
        supervisor.run(on_events=on_events, stop_event=stop_event)
    )
    gamma_task: asyncio.Task[None] | None = None
    if (
        config.gamma_tag_ids
        and config.gamma_poll_interval_seconds > 0
    ):
        gamma_task = asyncio.create_task(
            _run_gamma_periodic_loop(
                gamma_worker=gamma_worker,
                tag_ids=config.gamma_tag_ids,
                interval_seconds=config.gamma_poll_interval_seconds,
                backoff_max_seconds=config.gamma_poll_backoff_max_seconds,
                jitter_ratio=config.gamma_poll_jitter_ratio,
                on_events=on_events,
                stop_event=stop_event,
                gamma_last_success_at=gamma_last_success_at,
            )
        )
    _emit_startup_logs(
        config=config,
        alert_by_id=alert_by_id,
        channel_bindings=channel_bindings,
    )

    try:
        if config.gamma_tag_ids:
            metadata_events = await gamma_worker.poll_once(
                tag_ids=config.gamma_tag_ids
            )
            gamma_last_success_at["at"] = datetime.now(timezone.utc)
            await on_events(metadata_events)
        await asyncio.shield(supervisor_task)
    except asyncio.CancelledError:
        raise
    finally:
        stop_event.set()
        if gamma_task is not None:
            gamma_task.cancel()
            try:
                await gamma_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("gamma_periodic_loop_failed")
        await _shutdown_supervisor(
            stop_event=stop_event,
            supervisor_task=supervisor_task,
            ws_client=ws_client,
        )
        _emit_json_log(
            "shutdown",
            {
                "counters": counters.__dict__,
                "runtime_metrics": observability.snapshot(),
                "ingestion_metrics": ingest_metrics.snapshot().__dict__,
                "mode": "dry_run" if not config.execute_sends else "live",
            },
        )


def main() -> None:
    args = _parse_args()
    try:
        config = _build_config(args)
    except (ValidationError, ValueError) as exc:
        raise SystemExit(f"Invalid runtime configuration: {exc}")
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass


def _parse_csv(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_int_csv(value: str | None) -> list[int]:
    if value is None or not value.strip():
        return []
    parsed: list[int] = []
    for part in value.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        parsed.append(int(stripped))
    return parsed


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value.strip())


def _parse_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value.strip())


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()
