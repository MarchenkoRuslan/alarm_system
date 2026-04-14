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

## Minimal module map

- `ingestion/`
  - `polymarket_ws_adapter.py`
  - `reconnect_supervisor.py`
  - `gamma_sync_worker.py`
- `pipeline/`
  - `canonical_mapper.py`
  - `event_dispatcher.py`
- `compute/`
  - `prefilter_index.py`
  - `volume_window.py`
  - `scenario_c_deferred_watch.py`
- `rules/`
  - `dsl_evaluator.py`
  - `reason_builder.py`
- `delivery/`
  - `queue_producer.py`
  - `providers/telegram_provider.py`
  - `provider_registry_bootstrap.py`
- `observability/`
  - `metrics.py`
  - `tracing.py`

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
