# alarm_system

Copyright (c) 2026 Ruslan Marchanka. All rights reserved.

MVP custom-alert system for prediction markets (current scope: Polymarket only).

The project provides a contract-first and architecture-first foundation for:

- real-time market event ingestion;
- canonical normalization;
- signal computation and rule evaluation (DSL);
- dedup/cooldown and explainability;
- channel-agnostic delivery (MVP provider: Telegram).

## Quick start

```bash
pip install -e ".[ingestion,dev]"
pytest
```

CLI command for ingestion:

```bash
run-ingestion --asset-id <ASSET_ID> [--gamma-tag-id <TAG_ID>]
```

CLI command for the full production pipeline:

```bash
run-service [--dry-run]
```

CLI command for interactive API (Swagger + Telegram webhook):

```bash
run-api
```

Interactive API startup applies SQL migrations from `migrations/*.sql`
automatically when `ALARM_POSTGRES_DSN` is configured.
`ALARM_ENV` controls storage fallback policy:

- `dev`/`test`: in-memory fallback is allowed when `ALARM_POSTGRES_DSN` is empty;
- `staging`/`prod`: API fails fast without `ALARM_POSTGRES_DSN`.
- `ALARM_AUTO_APPLY_SQL_MIGRATIONS=true|false` controls startup SQL migration
  bootstrap for `run-api`.

Alert write contract for internal API:

- `POST /internal/alerts` creates a new alert (create-only).
- `PUT /internal/alerts/{alert_id}` updates existing alert and requires
  `expected_version`.

## Project structure

```text
src/alarm_system/
├── __init__.py                  # public package contracts
├── canonical_event.py           # CanonicalEvent, build_event_id, build_payload_hash
├── adapters.py                  # MarketAdapter, AdapterRegistry
├── rules_dsl.py                 # DSL v1, trigger keys, cooldown
├── dedup.py                     # deterministic dedup/cooldown keys
├── entities.py                  # User, Alert, Market, Trade, and others
├── alert_store.py               # Postgres truth + Redis cache storage layer
├── delivery.py                  # DeliveryPayload, DeliveryProvider, ProviderRegistry
├── delivery_runtime.py          # trigger audit + cooldown + idempotent dispatch
├── backpressure.py              # bounded queue saturation controller (70/90/recovery)
├── state.py                     # dedup/cooldown/suppression/deferred Redis abstractions
├── observability.py             # runtime SLO checks + metric series/counters
├── load_harness.py              # locked-profile load smoke (200 eps + burst)
├── rollback_drill.py            # rollback smoke procedure (freeze/replay/parity)
├── run_api.py                   # FastAPI entrypoint (run-api)
├── providers/
│   ├── __init__.py
│   └── telegram.py              # MVP Telegram provider
├── api/
│   ├── app.py                   # FastAPI app factory + env-based wiring
│   ├── migrations.py            # startup SQL migration bootstrap
│   └── routes/
│       ├── alerts.py            # internal CRUD API
│       └── telegram_webhook.py  # Telegram interactive webhook commands
├── compute/
│   ├── features.py              # MVP feature extraction from canonical payload
│   └── prefilter.py             # candidate prefilter index (rule_type, tag, event_type)
├── rules/
│   ├── evaluator.py             # DSL predicate evaluation + TriggerReason
│   ├── deferred_watch.py        # delayed-liquidity watch lifecycle
│   └── runtime.py               # prefilter + evaluator orchestration
├── migrations/
│   ├── 0001_alert_config_tables.sql # initial Postgres schema for API config storage
│   └── 0002_phase3_entry.sql        # delivery_attempts, deferred_watches, trigger_audit
├── schemas/
│   └── canonical_event.v1.schema.json
└── ingestion/
    ├── metrics.py               # in-memory counters/gauges
    ├── validation.py            # JSON Schema validation
    ├── run_ingestion.py         # CLI entrypoint
    └── polymarket/
        ├── adapter.py           # PolymarketMarketAdapter
        ├── mapper.py            # wire -> canonical mapping
        ├── supervisor.py        # heartbeat, reconnect, batch dedup
        ├── ws_client.py         # WebSocket transport
        └── gamma_sync.py        # Gamma metadata polling
```

## Architectural source of truth

Read in this order:

1. `docs/architecture/verified-facts.md`
2. `docs/architecture/adr/ADR-SET-v1.md`
3. `docs/architecture/canonical-schema-versioning.md`
4. `docs/architecture/rules-dsl-v1.md`
5. `docs/architecture/mvp-scope-and-delivery-plan.md`

Also useful:

- `docs/architecture/implementation-blueprint.md`
- `docs/architecture/agent-runbook.md`
- `docs/architecture/architecture-deck.md`
- `docs/architecture/ingestion-implementation-notes.md`
- `docs/architecture/compute-rules-baseline.md`
- `docs/architecture/state-delivery-entry-design.md`
- `docs/architecture/hardening-gap-matrix.md`

## MVP boundaries

- Market: Polymarket only.
- SLA: p95 `source_event_ts -> delivery_enqueue_ts <= 1s`.
- Baseline compute/rules checks are revalidated with: `pytest tests/compute tests/rules`.
- Hardening gate (SLO/backpressure/reconnect/rollback):
  - `pytest tests/test_runtime_metrics.py tests/test_observability.py tests/test_backpressure_runtime.py tests/test_load_harness.py tests/ingestion/test_polymarket_reconnect.py tests/test_rollback_drill.py`
- Operational commands:
  - `run-load-gate --profile smoke`
  - `run-load-gate --profile long --max-runtime-sec 900 --progress-every-events 2000`
  - `run-rollback-gate`
  - `run-service --dry-run` (staged rollout step 1)
  - `run-api` (interactive mode, internal CRUD + webhook)
  - CI/manual job: `.github/workflows/load-and-rollback-gate.yml`
  - CI/manual job: `.github/workflows/deploy-readiness.yml`
- Presets A/B/C are examples; the rules engine remains customizable.
- Baseline minimal signal set:
  - `price_return_1m_pct`
  - `price_return_5m_pct`
  - `spread_bps`
  - `book_imbalance_topN`
  - `liquidity_usd`

## Change principles

- Do not break canonical schema/DSL without a versioning procedure.
- Preserve explainability (`reason_json`) for every trigger.
- Keep dedup/cooldown deterministic and channel-aware.
- Document all assumptions/fallbacks in `docs/architecture/`.

## For developers

- Public package contracts are exported via `src/alarm_system/__init__.py`.
- Before changes, check `AGENTS.md` and architecture docs.
- For extensions (new signal/channel/source), update docs/ADR first, then code.
- Ingestion fixtures/tests are in `tests/fixtures/polymarket/` and `tests/ingestion/`.

## Docker Compose quick start (single-host)

1. Copy `.env.example` to `.env` and fill required values:
   - `ALARM_ENV` (`dev`, `test`, `staging`, `prod`)
   - `ALARM_ASSET_IDS`
   - `ALARM_TELEGRAM_BOT_TOKEN`
   - `ALARM_POSTGRES_DSN` (для интерактивного API и source-of-truth конфигов)
2. Optionally replace sample configs in `deploy/config/`:
   - `rules.sample.json`
   - `alerts.sample.json`
   - `channel-bindings.sample.json`
3. Dry-run pre-production:
   - `docker compose --profile dry-run up --build alarm-service-dry-run redis`
4. Live startup:
   - `docker compose up --build -d redis alarm-service`
5. Interactive API startup (Swagger + webhook, profile-based):
   - `docker compose --profile interactive up --build -d postgres redis alarm-api`
   - production note: set `ALARM_ENV=prod` in `.env`
6. Basic operations:
   - `docker compose logs -f alarm-service`
   - `docker compose logs -f alarm-api`
   - `docker compose restart alarm-service`
   - `docker compose restart alarm-api`
   - `docker compose down`

## Interactive mode limitations (current MVP)

- Internal API endpoints under `/internal/*` currently rely on network perimeter
  and do not yet enforce application-level auth.
- Telegram webhook currently does not validate secret token headers.
- SQL migrations are auto-applied at startup; migration lifecycle is not yet
  managed by Alembic.

## Staged rollout (MVP)

1. Dry-run (`run-service --dry-run` or `alarm-service-dry-run`) and verify:
   - no burst growth of `skipped_backpressure`;
   - p95 `event_to_enqueue_ms <= 1000`;
   - no unexpected fatal errors.
2. Limited live window (short controlled traffic window).
3. Full enablement after a green window and successful `deploy-readiness` gate.

## Preflight checklist before live

- `docker compose --profile dry-run config` passes without errors.
- `.env` contains `ALARM_ASSET_IDS` and `ALARM_TELEGRAM_BOT_TOKEN`.
- Runtime files are aligned by `rule_id + version` identities:
  - `deploy/config/rules.sample.json`
  - `deploy/config/alerts.sample.json`
  - `deploy/config/channel-bindings.sample.json`
- Dry-run service emits `startup_checks` and `startup` logs.

## Rollback (hybrid)

Path A (build-only, without registry):

- `git checkout <stable-tag>`
- `docker compose build alarm-service`
- `docker compose up -d alarm-service`

Path B (if using a registry and image tags):

- pin `image: <repo>/<name>:<stable-tag>` in `docker-compose.yml`
- `docker compose pull alarm-service`
- `docker compose up -d alarm-service`
