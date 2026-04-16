# State And Delivery Entry Design

Date: 2026-04-16

## Scope

Документ фиксирует минимальный входной срез state/delivery контура:

1. deterministic trigger key + Redis dedup/cooldown в runtime-пути;
2. trigger audit с `reason_json`;
3. channel-aware delivery path (MVP provider: Telegram);
4. delivery attempts audit для retries/failures.

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

- Rule runtime резервирует dedup key до выдачи `TriggerDecision`.
- Повторный trigger в dedup TTL не возвращается в output path.
- `TriggerDecision` несет:
  - `tenant_id`;
  - `trigger_key`;
  - `event_ts` (SLO start reference).
- Для delayed-liquidity:
  - crossing под активным `suppress_if` не сжигает watch;
  - watch помечается `fired` только после первого non-suppressed trigger.

## Delivery contract updates

- `DeliveryDispatcher`:
  - пишет trigger audit (`alert_id`, `rule_id`, `rule_version`, `reason_json`) с `save_once` по `trigger_key`;
  - применяет channel-aware cooldown;
  - делает idempotency reservation по `(trigger_key, channel, destination)`;
  - пишет `DeliveryAttempt` на каждый send/retry/fail;
  - использует `alert.cooldown_seconds` как source of truth.

## Observability checks

- `event_to_enqueue_ms` p95 через `RuntimeObservability`.
- Gate check: `event_to_enqueue_ms` p95 <= 1000ms.
- Дополнительно:
  - `rule_eval_ms`;
  - `queue_lag_ms`;
  - `dedup_hits_total`.
