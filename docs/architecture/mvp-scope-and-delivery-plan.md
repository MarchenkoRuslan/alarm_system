# MVP Scope Lock And Delivery Plan (Polymarket-only)

## Scope lock

### In scope

1. **Ingestion**
   - Polymarket WS adapter with heartbeat/reconnect/resubscribe.
   - Gamma metadata sync outside the hot path.
2. **Canonical pipeline**
   - Canonical event v1 validation.
   - Deterministic shaping and trace propagation.
3. **Compute + rules**
   - DSL evaluation with prefilter -> predicate pipeline.
   - Explainability (`reason_json`) for every trigger.
4. **State**
   - Redis keys for dedup/cooldown/suppression/deferred-watch.
   - Postgres for durable domain entities.
5. **Delivery**
   - Channel-agnostic delivery runtime.
   - Telegram as MVP provider.
6. **Observability**
   - p95 enqueue SLO, queue lag, eval latency, ingest lag, dedup hit rate.

### Out of scope

- Multi-exchange ingestion.
- Multi-region rollout.
- Production non-Telegram providers.
- Heavy offline models for signals.

## Extension-ready boundaries

- A new source is added only via `MarketAdapter` + `AdapterRegistry`.
- Rule engine/dedup/cooldown/delivery remain source-agnostic.
- Enabling a new source requires:
  1. ADR update
  2. canonical fixtures
  3. replay/duplicate tests
  4. SLO re-validation.

## Identity model

- `rule_id` + `rule_version` = evaluation identity.
- `alert_id` = user routing identity.
- Trigger audit stores both `alert_id` and immutable `(rule_id, rule_version)`.

## Locked load profile baseline

- sustained flow: **200 events/sec**;
- active alerts: **5,000** (`~40%` volume_spike, `~40%` trader_position_update, `~20%` new_market_liquidity);
- burst multiplier: **3x** for **60s**;
- reconnect storm:
  - 3 transport drops in 120s;
  - resubscribe after each reconnect;
  - replay of the last 10% of source events.

This profile is used for parity checks and SLO verification.

## Domain delivery sequence

1. **Ingestion core**
   - mapping contract tests are green;
   - reconnect storm without duplicate enqueue.
2. **Compute + rules**
   - deterministic replay parity on a recorded window;
   - one-shot delayed-liquidity crossing.
3. **State + delivery**
   - Redis dedup/cooldown keys;
   - trigger audit with `reason_json`;
   - idempotent send behavior on repeated replay.
4. **Hardening**
   - p95 `event_to_enqueue_ms` is green on locked profile;
   - backpressure warning/critical/recovery tests are green;
   - rollback drill is valid.

## Evidence snapshot (2026-04-16)

- compute/rules parity:
  - `tests/rules/test_runtime_replay.py::test_replay_parity_is_deterministic_under_duplicate_noise`
  - fixture `tests/rules/fixtures/replay_window.json`
- state/delivery correctness:
  - `tests/test_delivery_runtime.py::test_dispatch_is_idempotent_for_same_trigger_channel_destination`
  - `tests/test_delivery_runtime.py::test_retry_and_failure_attempts_are_persisted`
  - `tests/rules/test_runtime_state_store.py::test_redis_trigger_audit_store_save_once_semantics`
- hardening gate:
  - `tests/test_load_harness.py::test_locked_profile_smoke_meets_slo`
  - `tests/test_backpressure_runtime.py::test_warning_state_acceptance_keeps_dispatch_correct`
  - `tests/ingestion/test_polymarket_reconnect.py::test_reconnect_storm_with_partial_replay_keeps_unique_emits`
  - `tests/test_rollback_drill.py::test_rollback_drill_smoke_passes`

## SLO and metrics

- Primary KPI: p95 `source_event_ts -> delivery_enqueue_ts <= 1000ms`.
- Required metrics:
  - `event_to_enqueue_ms{scenario,rule_type,channel,source,event_type}`
  - `ingest_lag_ms{source,event_type}`
  - `rule_eval_ms{rule_type,scenario}`
  - `queue_lag_ms{queue_name,channel}`
  - `prefilter_hit_ratio{rule_type,scenario}`
  - `dedup_hits_total{rule_type,scenario,channel}`

## Rollback criteria

- sustained SLO breach without stabilization;
- persistent critical saturation;
- confirmed duplicate-send or missing-trigger incident.

Rollback steps:
1. disable non-critical enrichments;
2. revert to last stable release;
3. replay checkpoint window;
4. run parity verification before restore.
