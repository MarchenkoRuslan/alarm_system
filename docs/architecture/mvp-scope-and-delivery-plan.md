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
   - Scenarios A/B/C.
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
- Advanced quant signals beyond A/B/C.
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

## Product scenarios (locked)

1. **A: Trader position updates**
   - Trigger on open/close/increase/decrease position.
   - Filters: `smart_score > 80`, `account_age_days > 365`, category `Politics`.
2. **B: Volume spike 5m**
   - Scope: Iran-tagged markets only.
   - Rolling 5m volume baseline + spike threshold.
3. **C: New market + delayed liquidity crossing**
   - Categories: `Politics`, `Esports`, `Crypto`.
   - Arm on `market_created`, fire once on first `liquidity >= 100000`.
   - Arm source policy: prefer WS `new_market` events; fallback to Gamma discovery sync if WS custom feature stream is unavailable.

## Non-functional requirements (hard)

- **Latency SLO**: p95 `source_event_ts -> delivery_enqueue_ts <= 1000ms`.
- **Correctness**:
  - no duplicate sends for same dedup key;
  - scenario C fires once per `(alert_id, market_id)`;
  - rule version is immutable in trigger audit.
- **Resilience**:
  - WS reconnect/resubscribe without duplicate sends;
  - at-least-once ingestion + idempotent triggering.

## SLO measurement spec (authoritative)

- `source_event_ts` is the canonical `event_ts` from [schemas/canonical_event.v1.schema.json](schemas/canonical_event.v1.schema.json).
- `delivery_enqueue_ts` is the timestamp when a `DeliveryPayload` is persisted/enqueued.
- Scenario mapping:
  - **A**: start from `position_update.event_ts`.
  - **B**: start from triggering market event that closes/evaluates the 5m window (`trade`/`orderbook_delta`/`liquidity_update`, as configured by signal implementation).
  - **C**: start from the *crossing* event (`liquidity_update.event_ts`) that first meets threshold, not from `market_created`.
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
- Add seed fixtures for A/B/C scenarios.
- Lock load profile contract:
  - target events/sec,
  - active alerts count,
  - burst multiplier,
  - reconnect storm scenario shape.

### Phase 1: Ingestion core
- WS adapter with heartbeat/reconnect/resubscribe.
- Canonical mapping and tracing.
- Metadata sync worker with freshness tracking.
- Gate to exit Phase 1:
  - contract tests green for canonical mapping fixtures;
  - reconnect/resubscribe storm test passes without duplicate enqueue.

### Phase 2: Compute + rules
- Prefilter index builder.
- Scenario A/B evaluators.
- Scenario C deferred watch lifecycle.
- Gate to exit Phase 2:
  - deterministic replay parity on recorded fixture window;
  - Scenario C one-shot behavior verified under delayed crossing.

### Phase 3: Dedup/cooldown/delivery
- Redis dedup/cooldown keys.
- Trigger persistence and explainability audit.
- Delivery queue + Telegram provider via provider registry.
- Gate to exit Phase 3:
  - delivery attempts persisted for all retries/failures;
  - idempotent send behavior validated with repeated trigger replay.

### Phase 4: SLO hardening
- Load and burst tests.
- Reconnect storm tests.
- Latency budget tuning and backpressure tuning.
- Gate to exit Phase 4:
  - p95 `event_to_enqueue_ms` green on locked load profile;
  - backpressure warning/critical/recovery tests green;
  - rollback drills validated.

## Rollback criteria (operational)

- Trigger rollback if any condition holds for configured observation window:
  - p95 `event_to_enqueue_ms` violates SLO with no stabilization trend;
  - queue remains in critical saturation state after mitigation;
  - duplicate-send rate exceeds dedup error budget;
  - missing-trigger incident confirmed for A/B/C critical path.
- Rollback actions:
  1. disable non-critical enrichments;
  2. revert to previous stable release;
  3. replay buffered events from checkpoint window;
  4. run parity verification before re-enable.

## Exit checklist

- Contract tests green.
- Scenario tests green (A/B/C).
- Replay tests show deterministic trigger outcomes.
- p95 enqueue SLO green under locked profile-driven load.
- Runbook updated with incident actions and rollback plan.
