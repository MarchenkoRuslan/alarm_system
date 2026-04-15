# Phase 3 Entry Design (Redis + Delivery Audit)

Date: 2026-04-16

## Scope

Этот документ фиксирует минимальный входной срез в Phase 3:

1. deterministic trigger key + Redis dedup/cooldown в runtime-пути;
2. trigger audit с `reason_json`;
3. channel-aware delivery path (MVP provider: Telegram);
4. delivery attempts audit для retries/failures.

## Redis key schema

- `alarm:dedup:{trigger_key}`  
  TTL: `max(cooldown_seconds, bucket_seconds) + safety_margin`.
- `alarm:cooldown:cooldown:{tenant_id}:{rule_id}:{rule_version}:{scope_id}:{channel}`  
  TTL: `cooldown_seconds`.
- `alarm:suppress:{alert_id}:{scope_id}:suppress:{idx}`  
  Value: unix timestamp `active_until`; TTL до окончания suppression window.
- `alarm:deferred_watch:{alert_id}:{market_id}`  
  JSON payload: `target_liquidity_usd`, `armed_at`, `expires_at`, `fired_at`.

## Runtime contract updates

- Rule runtime резервирует dedup key до выдачи `TriggerDecision`.
- Повторный триггер в пределах dedup TTL не возвращается в output path.
- Dedup TTL для runtime берется из `bucket_seconds + safety_margin` (без зависимости от rule cooldown).
- `TriggerDecision` теперь несет:
  - `tenant_id`;
  - `trigger_key`;
  - `event_ts` (SLO start reference).
- Для delayed-liquidity:
  - crossing при активном `suppress_if` не сжигает watch;
  - watch помечается `fired` только после первого non-suppressed trigger.

## Delivery contract updates

- `DeliveryDispatcher`:
  - пишет trigger audit (`alert_id`, `rule_id`, `rule_version`, `reason_json`) с `save_once` по `trigger_key`;
  - применяет channel-aware cooldown;
  - делает durable idempotency reservation по `(trigger_key, channel, destination)`;
  - пишет `DeliveryAttempt` на каждый send/retry/fail.
  - cooldown source of truth: `alert.cooldown_seconds`.

## Observability checks (Phase 3 entry)

- `event_to_enqueue_ms` p95 check включен через `RuntimeObservability`.
- Gate check: `event_to_enqueue_ms` p95 <= 1000ms.
- Дополнительно должны трекаться:
  - `rule_eval_ms`;
  - `queue_lag_ms`;
  - `dedup_hits_total`.
