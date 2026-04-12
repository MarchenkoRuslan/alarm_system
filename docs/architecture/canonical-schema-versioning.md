# Canonical Schema v1 and Adapter Versioning Policy

## Scope

This policy applies to all source adapters that emit canonical events consumed by signal/rule/delivery layers.

## Canonical schema

- Active version: `1.0.0`
- JSON schema file: `schemas/canonical_event.v1.schema.json`
- Producer requirement: every emitted canonical event must validate against the active schema

## Version semantics

- **MAJOR**: backward-incompatible contract changes (field removals, semantic changes)
- **MINOR**: backward-compatible additions (new optional fields, new non-breaking enum values)
- **PATCH**: typo/doc fixes with no runtime contract impact

## Compatibility rules

1. **Producers** validate strictly (`additionalProperties: false`, `extra="forbid"`) to catch errors at source.
2. **Consumers** must tolerate unknown fields (use `extra="ignore"` on the receiving side) so that events from schema N+1 can be parsed by consumers built for schema N.
3. Required fields cannot be removed in MAJOR lifetime.
4. Enum expansion in MINOR is allowed only with consumer fallback behavior.
5. `schema_version` must be embedded in each event.

## Adapter release rules

1. Every adapter has independent version (example: `polymarket-ws@1.3.0`).
2. Adapter emits both `trace.adapter_version` and `schema_version`.
3. Adapter upgrades are blocked if schema validation fails in contract tests.
4. One adapter release can support at most two schema majors during migration (`N` and `N+1`).

## Migration protocol

1. Publish new schema and migration notes.
2. Enable dual-write in adapter (`schema_version=N` and `N+1`) to separate topics.
3. Upgrade consumers and verify replay parity.
4. Switch readers to `N+1`.
5. Decommission `N` after retention window.

## Contract test minimum

- Golden payload fixtures for each source event type
- Validation tests against canonical schema
- Replay tests for out-of-order and duplicate source events
- Deterministic `payload_hash` and `event_id` generation checks
