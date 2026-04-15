# MVP Scope Lock and Delivery Plan (Polymarket-only, Senior Track)

## Scope lock

### In scope (must deliver)

1. **Realtime ingestion**
   - Polymarket Market WS adapter (heartbeat + reconnect + resubscribe).
   - Gamma metadata sync (tags/categories/liquidity) outside hot path.
2. **Canonical pipeline**
   - Canonical event v1 validation.
   - Deterministic event shaping and trace propagation.
3. **Rule/compute layer**
   - Reference presets (A/B/C-like templates) + generic DSL rule evaluation.
   - Two-phase evaluation: prefilter -> predicate evaluation.
   - Explainability (`reason_json`) for each trigger.
4. **State**
   - Redis: windows, dedup, cooldown, hot indexes.
   - Postgres: users/alerts/channel bindings/triggers/delivery attempts/deferred watches.
5. **Delivery**
   - Channel-abstracted delivery layer with provider registry.
   - Telegram provider as MVP provider.
6. **Observability**
   - p95 SLO to enqueue, queue lag, eval latency, ingest lag, dedup hit rate.

### Out of scope (defer)

- Second exchange integrations.
- Multi-region deployment.
- Advanced quant signals beyond initial preset/template set.
- Non-Telegram providers in production rollout (but architecture supports them).

## Extension-ready boundaries (locked now, scalable later)

- New market sources must be isolated behind `MarketAdapter` + `AdapterRegistry`.
- Rule engine, dedup, cooldown, and delivery layers must stay source-agnostic.
- New source rollout requires:
  1. ADR update,
  2. canonical mapping fixtures,
  3. replay and duplicate-stream tests,
  4. SLO re-validation under burst load.
- MVP runtime remains Polymarket-only until product sign-off.

## Identity model (rule vs alert)

- `rule_id` + `rule_version` define evaluation semantics and explainability identity.
- `alert_id` defines user-facing routing/subscription identity.
- Runtime bridge:
  - one `Alert` references exactly one immutable `(rule_id, rule_version)`;
  - trigger audit stores `alert_id`, `rule_id`, and `rule_version`;
  - dedup/cooldown keys remain rule-version aware.

## Reference alert presets (examples, not hard requirements)

1. **Example A: Trader position updates**
   - Trigger on open/close/increase/decrease position.
   - Filters: `smart_score > 80`, `account_age_days > 365`, category `Politics`.
2. **Example B: Volume spike 5m**
   - Scope: Iran-tagged markets only.
   - Rolling 5m volume baseline + spike threshold.
3. **Example C: New market + delayed liquidity crossing**
   - Categories: `Politics`, `Esports`, `Crypto`.
   - Arm on `market_created`, fire once on first `liquidity >= 100000`.
   - Arm source policy: prefer WS `new_market` events; fallback to Gamma discovery sync if WS custom feature stream is unavailable.

## General customization model (platform capability)

Users can define custom alerts by combining:

- signal family (`rule_type`) and expression tree (`AND`/`OR`/`NOT`);
- numeric thresholds and rolling windows;
- category/tag scopes and entity filters;
- cooldown/suppression controls;
- channel routing (`alert.channels`) with per-channel bindings.

Reference presets are only starter templates. The runtime path (prefilter -> eval -> dedup/cooldown -> enqueue) remains generic.

## User-facing configuration model (what can be selected)

For each alert, user can choose:

- **Preset or custom** rule authoring mode.
- **Signal family / rule type** and expression complexity (single condition or boolean tree).
- **Thresholds and windows** (for spike/momentum-like logic).
- **Market/trader filters** (tags, account age, smart score, etc.).
- **Delivery channels** (one or many) and per-channel destination.
- **Cooldown/suppression** anti-noise settings.
- **Optional delayed trigger mode** (arm now, fire on later threshold crossing).

## Minimal signal metric set (required, easy to implement)

Use only metrics that are directly available from Polymarket WS or Gamma metadata, without heavy modeling:

1. `price_return_1m_pct`
   - source: Polymarket WS market events (`last_trade_price` / `price_change`);
   - usage: short-horizon momentum trigger.
2. `price_return_5m_pct`
   - source: Polymarket WS market events (`last_trade_price` / `price_change`);
   - usage: filter short noise and confirm sustained move.
3. `spread_bps`
   - source: Polymarket WS `book` (best bid/ask);
   - usage: execution quality / slippage risk filter.
4. `book_imbalance_topN`
   - source: Polymarket WS `book` (bid vs ask depth on top-N levels);
   - usage: directional pressure signal.
5. `liquidity_usd`
   - source: Gamma markets metadata sync (`liquidity`);
   - usage: market-quality and delayed-trigger threshold guard.

Anything beyond this set (cross-market divergence, adaptive confidence models, complex trader scoring) is optional and deferred until after MVP profiling.

## Default threshold profiles (MVP presets)

Use three default sensitivity profiles so users can start without manual tuning:

1. **Conservative** (low noise, fewer triggers)
   - `price_return_1m_pct >= 2.0`
   - `price_return_5m_pct >= 4.0`
   - `spread_bps <= 80`
   - `abs(book_imbalance_topN) >= 0.30`
   - `liquidity_usd >= 250000`
   - `cooldown_seconds = 300`
2. **Balanced** (recommended default)
   - `price_return_1m_pct >= 1.2`
   - `price_return_5m_pct >= 2.5`
   - `spread_bps <= 120`
   - `abs(book_imbalance_topN) >= 0.20`
   - `liquidity_usd >= 100000`
   - `cooldown_seconds = 180`
3. **Aggressive** (more signals, higher noise)
   - `price_return_1m_pct >= 0.7`
   - `price_return_5m_pct >= 1.5`
   - `spread_bps <= 180`
   - `abs(book_imbalance_topN) >= 0.12`
   - `liquidity_usd >= 50000`
   - `cooldown_seconds = 90`

Profile selection is user-facing; implementation stays the same and only parameter values change.

## Non-functional requirements (hard)

- **Latency SLO**: p95 `source_event_ts -> delivery_enqueue_ts <= 1000ms`.
- **Correctness**:
  - no duplicate sends for same dedup key;
  - for delayed-liquidity alert patterns, trigger fires once per `(alert_id, market_id)`;
  - rule version is immutable in trigger audit.
- **Resilience**:
  - WS reconnect/resubscribe without duplicate sends;
  - at-least-once ingestion + idempotent triggering.

## SLO measurement spec (authoritative)

- `source_event_ts` is the canonical `event_ts` from [canonical_event.v1.schema.json](../../src/alarm_system/schemas/canonical_event.v1.schema.json).
- `delivery_enqueue_ts` is the timestamp when a `DeliveryPayload` is persisted/enqueued.
- Scenario mapping:
  - **Example A**: start from `position_update.event_ts`.
  - **Example B**: start from triggering market event that closes/evaluates the 5m window (`trade`/`orderbook_delta`/`liquidity_update`, as configured by signal implementation).
  - **Example C**: start from the *crossing* event (`liquidity_update.event_ts`) that first meets threshold, not from `market_created`.
- Time discipline:
  - all timestamps are UTC;
  - hosts run NTP sync;
  - events with invalid/future skew beyond configured tolerance are quarantined and excluded from SLO calculations.

## Metric catalog (minimum labels)

- `event_to_enqueue_ms{scenario,rule_type,channel,source,event_type}`
- `ingest_lag_ms{source,event_type}`
- `rule_eval_ms{rule_type,scenario}`
- `queue_lag_ms{queue_name,channel}`
- `prefilter_hit_ratio{rule_type,scenario}`
- `dedup_hits_total{rule_type,scenario,channel}`

## Latency budget (target envelope)

- WS ingest + normalize: **<= 120ms**
- Prefilter lookup + rule eval: **<= 300ms**
- Dedup + cooldown + persistence: **<= 220ms**
- Enqueue delivery job: **<= 120ms**
- Buffer (jitter/retry/scheduling): **<= 240ms**
- **Total p95 <= 1000ms**

## Capacity and backpressure strategy

1. Prefilter indexes by `(rule_type, tag_id, event_type)` to avoid full rule scans.
2. Bounded queues between stages; explicit queue lag metrics.
3. Drop/defer non-critical enrichment from hot path.
4. Delivery worker concurrency controlled by queue depth and provider rate limits.
5. If queue lag exceeds threshold:
   - degrade non-critical processing;
   - preserve trigger correctness first.

### Saturation policy (explicit contract)

- Queue utilization states:
  - **normal**: `< 70%` queue capacity.
  - **warning**: `70%-90%`.
  - **critical**: `> 90%`.
- Actions by state:
  - **warning**:
    - increase worker concurrency within configured ceiling;
    - enable safe micro-batching in non-blocking stages.
  - **critical**:
    - stop optional enrichments;
    - enforce priority on trigger-critical path;
    - reject/park non-critical background jobs until recovery.
- Recovery condition:
  - remain below `70%` for a full stabilization window before restoring degraded features.

### Backpressure acceptance tests

1. Warning-state test: queue at `~80%` keeps p95 enqueue under SLO with no trigger loss.
2. Critical-state test: queue at `>90%` preserves trigger correctness and dedup guarantees.
3. Recovery test: after load drops, degraded features restore without duplicate sends.

## Delivery plan (implementation order)

### Phase 0: Baseline contracts and migrations

- Finalize schema/entities/delivery contracts.
- Create DB migrations for `channel_bindings`, `delivery_attempts`, `deferred_watches`.
- Add seed fixtures for example alert presets (A/B/C-like templates).
- Lock load profile contract:
  - target events/sec,
  - active alerts count,
  - burst multiplier,
  - reconnect storm scenario shape.

### Locked load profile baseline (pre-prod reference)

The following baseline is locked for phase gates until explicitly revised:

- target flow: **200 events/sec** sustained;
- active alerts: **5,000** total (`~40%` volume_spike, `~40%` trader_position_update, `~20%` new_market_liquidity);
- burst multiplier: **3x** for **60s** windows;
- reconnect storm shape:
  - 3 transport drops within 120s,
  - resubscribe on every reconnect,
  - replay of the latest 10% source events after reconnect.

This profile is used for deterministic replay/parity checks and for phase-exit SLO verification.

### Phase 1: Ingestion core

- WS adapter with heartbeat/reconnect/resubscribe.
- Canonical mapping and tracing.
- Metadata sync worker with freshness tracking.
- Gate to exit Phase 1:
  - contract tests green for canonical mapping fixtures;
  - reconnect/resubscribe storm test passes without duplicate enqueue.

### Phase 2: Compute + rules

- Prefilter index builder.
- Implement initial evaluators for reference presets.
- Implement deferred watch lifecycle for delayed-liquidity patterns.
- Gate to exit Phase 2:
  - deterministic replay parity on recorded fixture window;
  - one-shot delayed-liquidity behavior verified under delayed crossing.

### Phase 2 completion snapshot (2026-04-16)

- Status: **gate criteria satisfied in test baseline**.
- Evidence:
  - recorded fixture replay parity test: `tests/rules/test_runtime_replay.py::test_replay_parity_is_deterministic_under_duplicate_noise` using `tests/rules/fixtures/phase2_replay_window.json`;
  - delayed crossing one-shot test: `tests/rules/test_runtime_replay.py::test_reference_a_b_c_rules_trigger_with_one_shot_delayed_liquidity`.
  - suppression window tests:
    - `tests/rules/test_runtime_replay.py::test_suppress_if_blocks_within_duration_then_allows_trigger`;
    - `tests/rules/test_runtime_replay.py::test_suppress_if_missing_signal_does_not_block_trigger`.
- Scope note:
  - `suppress_if` is implemented with phase-2 in-memory state; Redis-backed suppression state joins Phase 3 with dedup/cooldown integration.

### Phase 3: Dedup/cooldown/delivery

- Redis dedup/cooldown keys.
- Trigger persistence and explainability audit.
- Delivery queue + Telegram provider via provider registry.
- Gate to exit Phase 3:
  - delivery attempts persisted for all retries/failures;
  - idempotent send behavior validated with repeated trigger replay.

### Phase 3 entry slice (minimal sequence)

1. Integrate deterministic trigger key + Redis dedup/cooldown checks into runtime output path.
2. Persist trigger audit (`alert_id`, `rule_id`, `rule_version`, `reason_json`) before enqueue.
3. Resolve channel bindings and produce `DeliveryPayload` per channel through provider registry.
4. Add replay test that proves idempotent send behavior under repeated trigger window.

### Phase 3 completion snapshot (2026-04-16)

- Status: **gate criteria satisfied in test baseline**.
- Evidence:
  - idempotent replay behavior:
    - `tests/test_delivery_runtime.py::test_dispatch_is_idempotent_for_same_trigger_channel_destination`;
    - `tests/test_delivery_runtime.py::test_dispatch_is_idempotent_across_dispatcher_instances`.
  - delivery attempts persisted for retries/failures:
    - `tests/test_delivery_runtime.py::test_retry_and_failure_attempts_are_persisted`.
  - trigger audit (`reason_json`, immutable `rule_id`/`rule_version`, save-once):
    - `tests/test_delivery_runtime.py::test_dispatch_persists_reason_json_and_delivery_attempt`;
    - `tests/rules/test_runtime_phase3_state.py::test_redis_trigger_audit_store_save_once_semantics`.
  - enqueue boundary + SLO measurement start-point continuity:
    - `tests/test_delivery_runtime.py::test_runtime_decision_enqueue_boundary_records_slo_metric`.
- Scope note:
  - delivery runtime now separates enqueue/persist boundary from send execution (supports deferred execution path for queue/worker split in Phase 4).

### Phase 4: SLO hardening

- Load and burst tests.
- Reconnect storm tests.
- Latency budget tuning and backpressure tuning.
- Gate to exit Phase 4:
  - p95 `event_to_enqueue_ms` green on locked load profile;
  - backpressure warning/critical/recovery tests green;
  - rollback drills validated.

### Phase 4 completion snapshot (2026-04-16)

- Status: **gate criteria satisfied in test baseline**.
- Evidence:
  - locked-profile deterministic load smoke (`200 eps`, burst `3x`) + p95 gate:
    - `tests/test_phase4_load_harness.py::test_locked_profile_smoke_meets_slo`;
    - `src/alarm_system/load_harness.py::run_locked_profile_smoke`.
  - backpressure warning/critical/recovery acceptance tests:
    - `tests/test_backpressure_runtime.py::test_warning_state_acceptance_keeps_dispatch_correct`;
    - `tests/test_backpressure_runtime.py::test_critical_state_rejects_when_capacity_exceeded`;
    - `tests/test_backpressure_runtime.py::test_recovery_state_returns_to_normal_without_duplicates`.
  - reconnect storm (`3 drops`, resubscribe, partial replay) without duplicate outcomes:
    - `tests/ingestion/test_polymarket_reconnect.py::test_reconnect_storm_with_partial_replay_keeps_unique_emits`.
  - rollback smoke drill (freeze -> load gate -> replay parity -> idempotent replay):
    - `tests/test_rollback_drill.py::test_rollback_drill_smoke_passes`;
    - `src/alarm_system/rollback_drill.py::run_rollback_drill_smoke`.
- Scope note:
  - locked profile baseline keeps contract rates (`200 eps`, burst `3x`) while smoke runtime uses compressed windows for deterministic CI validation.

## Rollback criteria (operational)

- Trigger rollback if any condition holds for configured observation window:
  - p95 `event_to_enqueue_ms` violates SLO with no stabilization trend;
  - queue remains in critical saturation state after mitigation;
  - duplicate-send rate exceeds dedup error budget;
  - missing-trigger incident confirmed for active critical alert presets/rules.
- Rollback actions:
  1. disable non-critical enrichments;
  2. revert to previous stable release;
  3. replay buffered events from checkpoint window;
  4. run parity verification before re-enable.

## Exit checklist

- Contract tests green.
- Reference preset tests green (A/B/C-like templates).
- Replay tests show deterministic trigger outcomes.
- p95 enqueue SLO green under locked profile-driven load.
- Runbook updated with incident actions and rollback plan.
