# Polymarket Alerts Architecture Pack

Architecture docs for the MVP (Polymarket only). See [`../README.md`](../README.md) for a compact index.

## Domain map

- `ingestion` → intake and normalization of external events (WS + optional Gamma HTTP).
- `canonical` → event contract and schema versioning rules.
- `compute` → signal computation and prefilter candidates.
- `rules` → DSL evaluation, suppression, deferred-watch, explainability.
- `delivery` → trigger audit, cooldown/idempotency, dispatch via provider abstraction.
- `state` → in-memory/Redis state stores for dedup/cooldown/suppression/deferred-watch.
- `observability` → SLO and runtime metrics.

### Worker runtime (`run-worker` / `run-service`)

- **WebSocket** (`PolymarketIngestionSupervisor`): subscribed `ALARM_ASSET_IDS`, hot path for market events.
- **Gamma HTTP** (`GammaMetadataSyncWorker`): `GET /markets` on `gamma-api.polymarket.com` with `tag_id` filters.
  - **Bootstrap:** one `poll_once` at startup when `ALARM_GAMMA_TAG_IDS` is set.
  - **Periodic:** background task when `ALARM_GAMMA_POLL_INTERVAL_SECONDS` > 0 (requires non-empty tag IDs; jitter + backoff on fetch errors).
- **Single evaluation pipeline:** WebSocket and Gamma batches both call the same `on_events` handler, guarded by an **`asyncio.Lock`** so `RuleRuntime` and counters are not updated concurrently.

See [`service_runtime.py`](../../src/alarm_system/service_runtime.py) and [`railway-deploy.md`](railway-deploy.md) for environment variables.

## Glossary

- `rule` — trigger logic (`rule_id`, `rule_version`, expression, filters).
- `alert` — user subscription to a specific rule version (`alert_id`).
- `trigger` — fact of rule activation for a scope with explainability (`reason_json`).
- `scope` — dedup/cooldown scope, usually `market_id`.
- `prefilter` — preliminary candidate selection using low-cost indexes `(rule_type, event_type, tags)`.

## Source of truth (read order)

1. [`verified-facts.md`](verified-facts.md) — confirmed external Polymarket/Telegram constraints.
2. [`adr/ADR-SET-v1.md`](adr/ADR-SET-v1.md) — accepted architecture decisions.
3. [`canonical-schema-versioning.md`](canonical-schema-versioning.md) — schema/contract versioning policy.
4. [`rules-dsl-v1.md`](rules-dsl-v1.md) — DSL contract, explainability, dedup/cooldown semantics.
5. [`mvp-scope-and-delivery-plan.md`](mvp-scope-and-delivery-plan.md) — scope and delivery approach in domain terms.

**Also:**

- [`agent-runbook.md`](agent-runbook.md) — operational checks and runbook.
- [`rule-catalog-migration.md`](rule-catalog-migration.md) — `ALARM_RULES_PATH` vs Postgres alerts, rollout.
- [`railway-deploy.md`](railway-deploy.md) — API/worker env split, deploy order.
- [`architecture-deck.md`](architecture-deck.md) — stakeholder-facing overview (Marp).

## Runtime anchors (code)

- [`../../src/alarm_system/schemas/canonical_event.v1.schema.json`](../../src/alarm_system/schemas/canonical_event.v1.schema.json)
- [`../../src/alarm_system/canonical_event.py`](../../src/alarm_system/canonical_event.py)
- [`../../src/alarm_system/rules_dsl.py`](../../src/alarm_system/rules_dsl.py)
- [`../../src/alarm_system/dedup.py`](../../src/alarm_system/dedup.py)
- [`../../src/alarm_system/entities.py`](../../src/alarm_system/entities.py)
- [`../../src/alarm_system/delivery.py`](../../src/alarm_system/delivery.py)
- [`../../src/alarm_system/adapters.py`](../../src/alarm_system/adapters.py)
- [`../../src/alarm_system/service_runtime.py`](../../src/alarm_system/service_runtime.py) — worker orchestration (WS + Gamma + rules + delivery)

## Recent updates (2026-04-19)

- Preset defaults are now `rule_type`-aware in the wizard and API examples.
- `new_market_liquidity` alert presets/filters keep only deferred-watch keys.
- SQL migration `0004_new_market_filters_cleanup.sql` cleans legacy
  `filters_json` keys for existing `new_market_liquidity` rows.
- Wizard state handling is hardened for stale/invalid `alert_type` values.
