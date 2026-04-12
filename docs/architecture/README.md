# Prediction Alerts Architecture Pack

This directory contains implementation artifacts for the custom alerts architecture.

## Contents

- `verified-facts.md` - validated external integration constraints
- `adr/ADR-SET-v1.md` - accepted architectural decisions
- `canonical-schema-versioning.md` - schema governance and adapter version policy
- `rules-dsl-v1.md` - rule contract, explainability, dedup/cooldown behavior
- `mvp-scope-and-delivery-plan.md` - locked MVP scope and 6-week execution plan
- `agent-runbook.md` - operational handbook for contributors/agents

## Related runtime artifacts

- `../../schemas/canonical_event.v1.schema.json` - canonical event schema
- `../../src/alarm_system/canonical_event.py` - typed Python model for canonical events
- `../../src/alarm_system/rules_dsl.py` - typed Python model and helper functions for rules
- `../../src/alarm_system/dedup.py` - dedup/cooldown key helpers
