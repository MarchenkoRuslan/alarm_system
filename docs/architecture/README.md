# Polymarket Alerts Architecture Pack

This directory contains source-of-truth architecture artifacts for the Polymarket-only MVP.

## Contents

- `verified-facts.md` - externally validated Polymarket integration constraints
- `adr/ADR-SET-v1.md` - accepted architecture decisions for low-latency MVP
- `canonical-schema-versioning.md` - canonical schema governance and adapter version policy
- `rules-dsl-v1.md` - DSL contract, explainability, dedup/cooldown, deferred watch semantics
- `mvp-scope-and-delivery-plan.md` - locked MVP scope, reference presets, and delivery roadmap
- `agent-runbook.md` - operational runbook and verification checklists
- `implementation-blueprint.md` - minimal module plan, latency/backpressure strategy, test matrix

## Related runtime artifacts

- `../../schemas/canonical_event.v1.schema.json` - canonical event schema
- `../../src/alarm_system/canonical_event.py` - typed Python model for canonical events
- `../../src/alarm_system/rules_dsl.py` - typed Python model and helper functions for rules
- `../../src/alarm_system/dedup.py` - dedup/cooldown key helpers
- `../../src/alarm_system/entities.py` - simple MVP entities (`Trade`, `Event`, `Market`, `User`, `Alert`)
- `../../src/alarm_system/delivery.py` - provider abstraction and delivery contracts
- `../../src/alarm_system/adapters.py` - source adapter abstraction and registry (`MarketAdapter`)
