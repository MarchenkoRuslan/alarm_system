# Polymarket Alerts Architecture Pack

This directory contains up-to-date architecture documents for the MVP (Polymarket only).

## Domain map

- `ingestion` -> intake and normalization of external events.
- `canonical` -> event contract and schema versioning rules.
- `compute` -> signal computation and prefilter candidates.
- `rules` -> DSL evaluation, suppression, deferred-watch, explainability.
- `delivery` -> trigger audit, cooldown/idempotency, dispatch via provider abstraction.
- `state` -> in-memory/Redis state stores for dedup/cooldown/suppression/deferred-watch.
- `observability` -> SLO and runtime metrics.

## Glossary

- `rule` - trigger logic (`rule_id`, `rule_version`, expression, filters).
- `alert` - user subscription to a specific rule version (`alert_id`).
- `trigger` - fact of rule activation for a scope with explainability (`reason_json`).
- `scope` - dedup/cooldown scope, usually `market_id`.
- `prefilter` - preliminary candidate selection using low-cost indexes.

## Source Of Truth

- `verified-facts.md` - confirmed external Polymarket constraints.
- `adr/ADR-SET-v1.md` - accepted architecture decisions.
- `canonical-schema-versioning.md` - schema/contract versioning policy.
- `rules-dsl-v1.md` - DSL contract, explainability, dedup/cooldown semantics.
- `mvp-scope-and-delivery-plan.md` - scope and delivery approach in domain terms.
- `implementation-blueprint.md` - practical map of modules and flows.
- `agent-runbook.md` - operational checks and runbook.
- `rule-catalog-migration.md` - `ALARM_RULES_PATH` vs Postgres alerts, one-time demo `rule_id` to canonical mapping, production rollout.

## Runtime Anchors

- `../../src/alarm_system/schemas/canonical_event.v1.schema.json`
- `../../src/alarm_system/canonical_event.py`
- `../../src/alarm_system/rules_dsl.py`
- `../../src/alarm_system/dedup.py`
- `../../src/alarm_system/entities.py`
- `../../src/alarm_system/delivery.py`
- `../../src/alarm_system/adapters.py`
