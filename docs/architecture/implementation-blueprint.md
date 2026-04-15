# Implementation Blueprint (Senior Minimalist)

## Goal

Provide a practical implementation blueprint that is:

- minimal in moving parts,
- explicit in performance guarantees,
- ready for controlled future extension.

## Design principles

1. Keep hot path short and deterministic.
2. Make correctness explicit (dedup, cooldown, explainability).
3. Separate fast ephemeral state from durable business state.
4. Add extension points only where they are proven to pay off.
5. Measure everything that can break SLO.

## Module map

### Implemented (phase 1)

- `src/alarm_system/` — core contracts
  - `canonical_event.py` — canonical event model, `build_event_id`, `build_payload_hash`
  - `adapters.py` — `MarketAdapter`, `AdapterRegistry`, `AdapterEnvelope`
  - `rules_dsl.py` — DSL models, trigger keys, cooldown
  - `dedup.py` — deterministic dedup/cooldown keys
  - `entities.py` — domain entities (User, Alert, Market, etc.)
  - `delivery.py` — `DeliveryPayload`, `DeliveryProvider`, `ProviderRegistry`
  - `schemas/canonical_event.v1.schema.json` — JSON Schema (package-data, loaded via `importlib.resources`)
- `src/alarm_system/ingestion/` — ingestion runtime
  - `metrics.py` — in-memory counters/gauges/timings
  - `validation.py` — JSON Schema validation with pydantic fallback
  - `run_ingestion.py` — CLI entrypoint (`run-ingestion`)
- `src/alarm_system/ingestion/polymarket/` — Polymarket adapter
  - `ws_client.py` — WS transport
  - `supervisor.py` — heartbeat watchdog, reconnect loop, batch dedup
  - `adapter.py` — `PolymarketMarketAdapter`
  - `mapper.py` — wire-to-canonical mapping
  - `gamma_sync.py` — Gamma metadata polling

### Implemented (phase 2)

- `src/alarm_system/compute/`
  - `features.py` — extraction of MVP metric snapshot from canonical payload
    - includes event-side filter inputs (`smart_score`, `account_age_days`) for rules runtime checks
  - `prefilter.py` — coarse candidate index `(rule_type, tag, event_type)`
- `src/alarm_system/rules/`
  - `evaluator.py` — predicate evaluation with `TriggerReason` build
  - `deferred_watch.py` — in-memory deferred-watch lifecycle (arm/fire/expire)
  - `suppression.py` — in-memory `suppress_if` duration window store (`alert_id + scope_id + condition index`)
  - `runtime.py` — prefilter + strict filters + evaluator + deferred-watch orchestration
    - prefilter index lifecycle: build once via `set_bindings()/load_bindings()`, evaluate without per-event rebuild
    - applies `category_tags`, `iran_tag_only`, `min_smart_score`, `min_account_age_days` before predicate evaluation
    - applies `suppress_if` after predicate match and before trigger decision build
  - phase-2 replay fixture gate: `tests/rules/fixtures/phase2_replay_window.json` + `tests/rules/test_runtime_replay.py`

### Deferred from phase 2 to phase 3 (explicit)

- Durable deferred-watch storage remains deferred; phase 2 uses `InMemoryDeferredWatchStore` only.
- `suppress_if` persistence backend remains deferred; phase 2 uses `InMemorySuppressionStore`, Phase 3 migrates state to Redis-aligned storage.

### Planned (next increments)

- Redis-backed deferred watch and dedup/cooldown integration in runtime.
- `delivery/providers/` — Telegram provider, queue producer.
- `observability/` — structured metrics, tracing.

## Data ownership

- **Redis**:
  - rolling windows,
  - dedup keys,
  - cooldown keys,
  - hot prefilter indexes.
- **Postgres**:
  - users, alerts, channel bindings,
  - deferred watches,
  - triggers and delivery attempts.
  - alert-rule bridge (`alert_id` -> `rule_id`, `rule_version`) for deterministic audit.

## Event handling flow

1. WS event received.
2. Canonical mapping + validation.
3. Prefilter candidate alerts by lightweight indexes.
4. Evaluate predicates only for candidates.
5. Build reason payload.
6. Dedup and cooldown.
7. Enqueue per alert channel via provider-agnostic payload.

## Performance checklist (must pass)

- Prefilter hit ratio documented under representative traffic.
- No sync HTTP calls in rule eval path.
- Redis round-trips bounded and measured.
- Queue lag alerting configured.
- p95 enqueue latency validated under burst profile.
- metric labels are stable and documented (`scenario`, `rule_type`, `channel`, `source`, `event_type`).

## Minimal signal feature contract (MVP)

Compute/evaluation path must support these low-cost market features first:

- `price_return_1m_pct`
- `price_return_5m_pct`
- `spread_bps`
- `book_imbalance_topN`
- `liquidity_usd`

Data-source mapping:

- WS market channel (`last_trade_price`, `price_change`, `book`) -> first 4 features.
- Gamma metadata sync (`liquidity`) -> `liquidity_usd`.

Default profile constants to ship in config:

- conservative: `{r1m: 2.0, r5m: 4.0, spread_bps_max: 80, imbalance_abs_min: 0.30, liquidity_usd_min: 250000, cooldown_s: 300}`
- balanced: `{r1m: 1.2, r5m: 2.5, spread_bps_max: 120, imbalance_abs_min: 0.20, liquidity_usd_min: 100000, cooldown_s: 180}`
- aggressive: `{r1m: 0.7, r5m: 1.5, spread_bps_max: 180, imbalance_abs_min: 0.12, liquidity_usd_min: 50000, cooldown_s: 90}`

Tuning rule:

- adjust one threshold group per release window and validate `event_to_enqueue_ms`, trigger rate, and dedup hit ratio before next change.

## Test strategy

### Unit

- mapping and schema tests;
- dedup/cooldown key determinism;
- rule evaluation edge cases;
- deferred watch transitions.

### Integration

- WS reconnect with duplicate source events;
- reference preset end-to-end (A/B/C-like templates) plus one non-template custom rule path;
- delivery attempt persistence on retry.

### Load

- sustained flow test;
- burst test;
- reconnect storm test.
- queue saturation test (warning/critical/recovery transitions).

## Release gates and rollback

- Release gate uses locked load profile (events/sec, active alerts, burst, reconnect storm).
- Promote only if p95 enqueue SLO and replay parity remain green on that profile.
- Keep checkpoint window for rapid rollback + replay recovery.

## Phase 3 entry plan (minimal)

1. Add runtime output stage for `build_trigger_key` + Redis dedup/cooldown checks.
2. Persist trigger audit record with `reason_json` and immutable `(rule_id, rule_version)`.
3. Fan out channel deliveries through `ProviderRegistry` with per-channel bindings.
4. Validate idempotent repeated replay behavior at delivery enqueue boundary.

## Future-safe extension points

- New source: implement `MarketAdapter`, register in `AdapterRegistry`, add mapping contract tests.
- New signal: add compute module + DSL preset docs.
- New delivery channel: add provider + binding migration + rate-limit policy.

## Anti-patterns (do not do)

- Full rules scan per event.
- Business logic mixed into adapter parsing.
- Channel-specific logic inside rule engine.
- Unbounded queues without backpressure metrics.
- Hidden retries without audit.
