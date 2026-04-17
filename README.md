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

App-oriented alias for the same worker runtime:

```bash
run-worker [--dry-run]
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
src/
├── alarm_system/                # shared core contracts/runtime modules
│   ├── api/                     # API internals
│   ├── compute/                 # signal extractors + prefilter
│   ├── ingestion/               # Polymarket ingestion adapters/workers
│   ├── providers/               # delivery providers (Telegram MVP)
│   ├── rules/                   # evaluator/runtime/deferred watch
│   ├── migrations/              # SQL bootstrap files
│   ├── schemas/                 # canonical JSON schemas
│   ├── service_runtime.py       # worker orchestration
│   ├── run_api.py               # API runtime entrypoint
│   └── apps/                    # namespaced app thin entrypoints
│       ├── api/main.py          # run-api wrapper
│       └── worker/main.py       # run-worker / run-service wrapper
```

## Logical split inside one repository

The repository is intentionally single-source, but split into two deployable
logical apps plus shared core:

- `alarm_system.apps.api` -> public FastAPI surface (`/docs`, `/health`, webhook).
- `alarm_system.apps.worker` -> ingestion/rules/delivery background runtime.
- `alarm_system/*` -> shared contracts, domain logic, persistence, and schemas.

Ownership convention for new modules:

- `alarm_system/apps/api/*`: API-only runtime wiring.
- `alarm_system/apps/worker/*`: worker-only runtime wiring.
- `alarm_system/*`: shared core modules used by both apps.

This keeps contract changes atomic while allowing independent deploy scaling.

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
- `docs/architecture/railway-deploy.md`
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
  - `run-worker --dry-run` (staged rollout step 1; `run-service` alias is still available)
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
   - `ALARM_TELEGRAM_WEBHOOK_URL` (public HTTPS URL for `/webhooks/telegram`)
   - `ALARM_TELEGRAM_WEBHOOK_SECRET` (optional, but recommended)
   - `ALARM_POSTGRES_DSN` (для интерактивного API и source-of-truth конфигов)
2. Optionally replace sample configs in `deploy/config/`:
   - `rules.sample.json`
   - `alerts.sample.json`
   - `channel-bindings.sample.json`
   - note: sample alerts are enabled by default; disable specific entries
     (`"enabled": false`) if you want a quiet bootstrap
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

## Railway deployment mapping (two services, one repo)

- API service:
  - Dockerfile: `Dockerfile.api`
  - start command: `run-api`
  - public domain: required
  - required env: `ALARM_TELEGRAM_WEBHOOK_URL`
  - optional env: `ALARM_TELEGRAM_WEBHOOK_SECRET`
- Worker service:
  - Dockerfile: `Dockerfile.worker`
  - start command: `run-worker` (or `run-service`)
  - public domain: not required

## Railway migration note (after split hardening)

If your Railway services were created before this split hardening:

1. API service
   - Dockerfile path -> `Dockerfile.api`
   - start command -> `run-api`
2. Worker service
   - Dockerfile path -> `Dockerfile.worker`
   - start command -> `run-worker` (alias `run-service` still works)
3. Redeploy API first, then Worker.

## Telegram bot commands

The interactive bot exposes a slash-command interface backed by
`/webhooks/telegram`. On API startup the commands are also registered
via Bot API `setMyCommands`, so Telegram clients show a command menu
automatically.

| Command | Purpose |
| --- | --- |
| `/start` | Bind this chat to your account (creates a `ChannelBinding`). |
| `/stop` | Unbind this chat. |
| `/help` | Full command reference. |
| `/status` | Summary of active alerts, bindings, mute state. |
| `/alerts [--all]` | List alerts (active by default; `--all` shows disabled too). |
| `/alert <id>` | Full details for one of your alerts. |
| `/bindings` | List your delivery channels. |
| `/history [N]` | Last N delivery attempts (default 10, max 50). |
| `/templates` | Enumerate available `/create` templates. |
| `/enable <id>` | Enable an alert (optimistic versioning). |
| `/disable <id>` | Disable an alert. |
| `/set_cooldown <id> <seconds>` | Update `cooldown_seconds`. |
| `/delete <id> [yes]` | Delete (confirmation required: repeat with `yes`). |
| `/create <template_id> [alert_id=...] [cooldown=...] [enabled=...]` | Create from a template in `ALERT_CREATE_EXAMPLES`. |
| `/create_raw <json>` | Create from a raw JSON payload (same shape as `POST /internal/alerts`). |
| `/mute <duration>` | Silence all your alerts (formats: `30m`, `2h`, `1d`; max `30d`). |
| `/unmute` | Cancel mute. |

Ownership contract: all write commands are forced to run against
`user_id` derived from the Telegram update. Users cannot address
alerts belonging to other accounts via the bot.

The `/mute` state is honored in the delivery pipeline: when active,
`DeliveryDispatcher` short-circuits with a `skipped_muted` stats
increment and a `delivery_skipped_muted_total` observability counter;
`event_to_enqueue_ms` is not emitted for the muted branch.

## Interactive mode limitations (current MVP)

- Internal API endpoints under `/internal/*` currently rely on network perimeter
  and do not yet enforce application-level auth.
- Telegram webhook bootstrap on API startup is best-effort (`setWebhook` fail-open):
  if Telegram API is temporarily unavailable, API still starts and serves HTTP endpoints.
- When `ALARM_TELEGRAM_WEBHOOK_SECRET` is configured, webhook validation is strict
  single-secret (`401` on mismatch).
- SQL migrations are auto-applied at startup; migration lifecycle is not yet
  managed by Alembic.

## Staged rollout (MVP)

1. Dry-run (`run-worker --dry-run` or `alarm-service-dry-run`) and verify:
   - no burst growth of `skipped_backpressure`;
   - p95 `event_to_enqueue_ms <= 1000`;
   - no unexpected fatal errors.
2. Limited live window (short controlled traffic window).
3. Full enablement after a green window and successful `deploy-readiness` gate.

## Preflight checklist before live

- `docker compose --profile dry-run config` passes without errors.
- `.env` contains `ALARM_ASSET_IDS`, `ALARM_TELEGRAM_BOT_TOKEN`,
  and `ALARM_TELEGRAM_WEBHOOK_URL`.
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
