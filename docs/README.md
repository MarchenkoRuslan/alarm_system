# Documentation index

All product and architecture documentation for `alarm_system` lives under **`docs/architecture/`**.

## Start here

| Audience | Path | Purpose |
|----------|------|---------|
| New contributors / agents | [`architecture/README.md`](architecture/README.md) | Domain map, glossary, links to code anchors |
| External API facts (Polymarket, Telegram) | [`architecture/verified-facts.md`](architecture/verified-facts.md) | Verified URLs and integration constraints |
| Decisions | [`architecture/adr/ADR-SET-v1.md`](architecture/adr/ADR-SET-v1.md) | ADRs (canonical, Redis/Postgres, delivery, worker pipeline) |
| Scope & ingestion | [`architecture/mvp-scope-and-delivery-plan.md`](architecture/mvp-scope-and-delivery-plan.md) | MVP boundaries, Gamma/WS behavior, dedup notes |
| Deploy (Railway / two services) | [`architecture/railway-deploy.md`](architecture/railway-deploy.md) | Env vars, API vs worker, rollout order |
| Rules & alerts migration | [`architecture/rule-catalog-migration.md`](architecture/rule-catalog-migration.md) | `rule_id`, Postgres vs file configs |
| Operations | [`architecture/agent-runbook.md`](architecture/agent-runbook.md) | SLO, load profile, checklists |
| Slides / overview | [`architecture/architecture-deck.md`](architecture/architecture-deck.md) | End-to-end diagram, examples (Marp) |

**API / OpenAPI examples:** the legacy example key `user_b_iran_volume_spike` was renamed to `user_b_volume_spike` (same `rule_type`: `volume_spike_5m`). Update clients that referenced the old key.

## Repository root

| File | Purpose |
|------|---------|
| [`../README.md`](../README.md) | Quick start, project tree, Docker, test commands |
| [`../AGENTS.md`](../AGENTS.md) | Agent/developer workflow and invariants |
| [`../.env.example`](../.env.example) | Environment variable templates (copy to `.env`) |

## Tests

Fixtures and tests under `tests/` are the executable spec when docs drift.
