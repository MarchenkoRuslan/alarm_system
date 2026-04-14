# Canonical Schema v1 and Adapter Versioning Policy

## Scope

This policy applies to market adapters that emit canonical events consumed by signal, rule, and delivery layers.
For MVP, only Polymarket adapters are production-enabled.

## Canonical schema

- Active version: `1.0.0`
- JSON schema file: `src/alarm_system/schemas/canonical_event.v1.schema.json`
- Producer requirement: every emitted canonical event must validate against active schema

## Version semantics

- **MAJOR**: backward-incompatible contract changes
- **MINOR**: backward-compatible additions (optional fields, new event types)
- **PATCH**: docs and typo fixes with no runtime impact

## Compatibility rules

1. Producers validate strictly (`additionalProperties=false`, `extra="forbid"`).
2. Consumers tolerate unknown fields (`extra="ignore"` recommended).
3. Required fields cannot be removed in MAJOR lifetime.
4. `schema_version` is mandatory in each event. In v1.0.0, `trace.adapter_version` is optional for backward compatibility and strongly recommended for all producers.
5. Event time must be UTC-normalized before publish.

## Polymarket event mapping baseline

Canonical `event_type` values currently supported:

- `market_snapshot`
- `orderbook_delta`
- `trade`
- `position_update`
- `liquidity_update`
- `market_created`
- `market_resolved`
- `wallet_activity`
- `metadata_refresh`

## Adapter release rules

1. Every adapter has independent version (e.g. `polymarket-ws@1.1.0`).
2. Adapter release is blocked if contract tests fail.
3. During migration, adapter can dual-write at most two schema majors (`N` and `N+1`).

## Migration protocol

1. Publish schema update and migration notes.
2. Enable dual-write if required.
3. Upgrade consumers and verify replay parity.
4. Switch readers to new version.
5. Decommission old version after retention window.

## Contract test minimum

- Golden payload fixtures for each Polymarket event type
- Validation tests against canonical schema
- Replay tests for duplicates/out-of-order events
- Deterministic `payload_hash` generation checks
