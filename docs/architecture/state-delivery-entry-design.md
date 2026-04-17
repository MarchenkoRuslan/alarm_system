# State And Delivery Entry Design

Date: 2026-04-16

## Scope

This document defines the minimum entry slice for the state/delivery domain:

1. deterministic trigger key + Redis dedup/cooldown in the runtime path;
2. trigger audit with `reason_json`;
3. channel-aware delivery path (MVP provider: Telegram);
4. delivery-attempt audit for retries/failures.

## Redis key schema

- `alarm:dedup:{trigger_key}`
  - TTL: `bucket_seconds + safety_margin`.
- `alarm:cooldown:cooldown:{tenant_id}:{rule_id}:{rule_version}:{scope_id}:{channel}`
  - TTL: `cooldown_seconds`.
- `alarm:suppress:{alert_id}:{scope_id}:suppress:{idx}`
  - value: unix timestamp `active_until`.
- `alarm:deferred_watch:{alert_id}:{market_id}`
  - JSON payload: `target_liquidity_usd`, `armed_at`, `expires_at`, `fired_at`.

## Runtime contract updates

- Rule runtime reserves dedup key before returning `TriggerDecision`.
- Repeated trigger inside dedup TTL is not returned to the output path.
- `TriggerDecision` carries:
  - `tenant_id`;
  - `trigger_key`;
  - `event_ts` (SLO start reference).
- For delayed liquidity:
  - crossing under active `suppress_if` does not consume the watch;
  - watch is marked `fired` only after the first non-suppressed trigger.

## Delivery contract updates

- `DeliveryDispatcher`:
  - writes trigger audit (`alert_id`, `rule_id`, `rule_version`, `reason_json`) with `save_once` by `trigger_key`;
  - applies channel-aware cooldown;
  - reserves idempotency by `(trigger_key, channel, destination)`;
  - writes `DeliveryAttempt` for each send/retry/fail;
  - uses `alert.cooldown_seconds` as source of truth.

## Observability checks

- `event_to_enqueue_ms` p95 through `RuntimeObservability`.
- Gate check: `event_to_enqueue_ms` p95 <= 1000ms.
- Additional metrics:
  - `rule_eval_ms`;
  - `queue_lag_ms`;
  - `dedup_hits_total`;
  - `delivery_skipped_muted_total` (per-channel when user mute is active);
  - `delivery_mute_check_failed_total` (fail-open counter when MuteStore raises).

## Retention for delivery audit log

`RedisDeliveryAttemptStore` bounds operational history to avoid unbounded growth:

- Per-attempt records `alarm:delivery_attempt:{id}` carry a 7-day TTL (`attempt_ttl_seconds`).
- The global index `alarm:delivery_attempt:index` is trimmed to `main_index_max_len` (default 10_000) entries on every write.
- Per-user indices `alarm:delivery_attempt:by_user:{user_id}` are trimmed to `user_index_max_len` (default 500) and back the `/history` Telegram command without scanning the global index.

`InMemoryDeliveryAttemptStore` applies a symmetric per-user cap so fixtures and dev
runs do not leak memory.
