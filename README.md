# alarm_system

MVP-система кастомных алертов для prediction markets (текущий scope: только Polymarket).

Проект закладывает контрактную и архитектурную основу для:

- realtime ingest рыночных событий;
- нормализации в канонический формат;
- вычисления сигналов и оценки правил (DSL);
- dedup/cooldown и explainability;
- channel-agnostic доставки (MVP провайдер: Telegram).

## Быстрый старт

```bash
pip install -e ".[ingestion,dev]"
pytest
```

CLI-команда для запуска ingestion:

```bash
run-ingestion --asset-id <ASSET_ID> [--gamma-tag-id <TAG_ID>]
```

CLI-команда для полного production pipeline:

```bash
run-service [--dry-run]
```

## Структура проекта

```text
src/alarm_system/
├── __init__.py                  # публичные контракты пакета
├── canonical_event.py           # CanonicalEvent, build_event_id, build_payload_hash
├── adapters.py                  # MarketAdapter, AdapterRegistry
├── rules_dsl.py                 # DSL v1, trigger keys, cooldown
├── dedup.py                     # deterministic dedup/cooldown keys
├── entities.py                  # User, Alert, Market, Trade и др.
├── delivery.py                  # DeliveryPayload, DeliveryProvider, ProviderRegistry
├── delivery_runtime.py          # trigger audit + cooldown + idempotent dispatch
├── backpressure.py              # bounded queue saturation controller (70/90/recovery)
├── state.py                     # dedup/cooldown/suppression/deferred Redis abstractions
├── observability.py             # runtime SLO checks + metric series/counters
├── load_harness.py              # locked-profile load smoke (200 eps + burst)
├── rollback_drill.py            # rollback smoke procedure (freeze/replay/parity)
├── providers/
│   ├── __init__.py
│   └── telegram.py              # MVP Telegram provider
├── compute/
│   ├── features.py              # MVP feature extraction from canonical payload
│   └── prefilter.py             # candidate prefilter index (rule_type, tag, event_type)
├── rules/
│   ├── evaluator.py             # DSL predicate evaluation + TriggerReason
│   ├── deferred_watch.py        # delayed-liquidity watch lifecycle
│   └── runtime.py               # prefilter + evaluator orchestration
├── schemas/
│   └── canonical_event.v1.schema.json
└── ingestion/
    ├── metrics.py               # in-memory counters/gauges
    ├── validation.py            # JSON Schema validation
    ├── run_ingestion.py         # CLI entrypoint
    └── polymarket/
        ├── adapter.py           # PolymarketMarketAdapter
        ├── mapper.py            # wire → canonical mapping
        ├── supervisor.py        # heartbeat, reconnect, batch dedup
        ├── ws_client.py         # WebSocket transport
        └── gamma_sync.py        # Gamma metadata polling
```

## Архитектурный source of truth

Читайте в этом порядке:

1. `docs/architecture/verified-facts.md`
2. `docs/architecture/adr/ADR-SET-v1.md`
3. `docs/architecture/canonical-schema-versioning.md`
4. `docs/architecture/rules-dsl-v1.md`
5. `docs/architecture/mvp-scope-and-delivery-plan.md`

Также полезно:

- `docs/architecture/implementation-blueprint.md`
- `docs/architecture/agent-runbook.md`
- `docs/architecture/architecture-deck.md`
- `docs/architecture/ingestion-implementation-notes.md`
- `docs/architecture/compute-rules-baseline.md`
- `docs/architecture/state-delivery-entry-design.md`
- `docs/architecture/hardening-gap-matrix.md`

## MVP boundaries

- Рынок: только Polymarket.
- SLA: p95 `source_event_ts -> delivery_enqueue_ts <= 1s`.
- Базовый compute/rules набор повторно подтвержден: `pytest tests/compute tests/rules`.
- Hardening gate (SLO/backpressure/reconnect/rollback):
  - `pytest tests/test_runtime_metrics.py tests/test_observability.py tests/test_backpressure_runtime.py tests/test_load_harness.py tests/ingestion/test_polymarket_reconnect.py tests/test_rollback_drill.py`
- Операционные команды:
  - `run-load-gate --profile smoke`
  - `run-load-gate --profile long --max-runtime-sec 900 --progress-every-events 2000`
  - `run-rollback-gate`
  - `run-service --dry-run` (staged rollout шаг 1)
  - CI/manual job: `.github/workflows/load-and-rollback-gate.yml`
  - CI/manual job: `.github/workflows/deploy-readiness.yml`
- Presets A/B/C являются примерами; движок правил остается кастомизируемым.
- Базовый минимальный набор сигналов:
  - `price_return_1m_pct`
  - `price_return_5m_pct`
  - `spread_bps`
  - `book_imbalance_topN`
  - `liquidity_usd`

## Принципы изменений

- Не ломать canonical schema/DSL без versioning-процедуры.
- Сохранять explainability (`reason_json`) для каждого trigger.
- Держать dedup/cooldown deterministic и channel-aware.
- Любые assumptions/fallback документировать в `docs/architecture/`.

## Для разработчиков

- Публичные контракты пакета экспортируются через `src/alarm_system/__init__.py`.
- Перед изменениями сверяйтесь с `AGENTS.md` и архитектурными документами.
- При расширении (новый сигнал/канал/источник) сначала обновляйте docs/ADR, затем код.
- Ingestion fixtures/tests находятся в `tests/fixtures/polymarket/` и `tests/ingestion/`.

## Docker Compose quick start (single-host)

1. Скопируйте `.env.example` в `.env` и заполните обязательные значения:
   - `ALARM_ASSET_IDS`
   - `ALARM_TELEGRAM_BOT_TOKEN`
2. При необходимости замените sample-конфиги в `deploy/config/`:
   - `rules.sample.json`
   - `alerts.sample.json`
   - `channel-bindings.sample.json`
3. Dry-run pre-prod:
   - `docker compose --profile dry-run up --build alarm-service-dry-run redis`
4. Live запуск:
   - `docker compose up --build -d redis alarm-service`
5. Базовые операции:
   - `docker compose logs -f alarm-service`
   - `docker compose restart alarm-service`
   - `docker compose down`

## Staged rollout (MVP)

1. Dry-run (`run-service --dry-run` или `alarm-service-dry-run`) и проверка:
   - нет burst роста `skipped_backpressure`;
   - p95 `event_to_enqueue_ms <= 1000`;
   - нет unexpected fatal errors.
2. Ограниченное live окно (короткий controlled traffic window).
3. Full enable после green-окна и успешного `deploy-readiness` gate.

## Preflight checklist перед live

- `docker compose --profile dry-run config` проходит без ошибок.
- В `.env` заполнены `ALARM_ASSET_IDS` и `ALARM_TELEGRAM_BOT_TOKEN`.
- Runtime файлы согласованы по идентичностям `rule_id + version`:
  - `deploy/config/rules.sample.json`
  - `deploy/config/alerts.sample.json`
  - `deploy/config/channel-bindings.sample.json`
- Dry-run сервис пишет логи `startup_checks` и `startup`.

## Rollback (hybrid)

Путь A (build-only, без registry):

- `git checkout <stable-tag>`
- `docker compose build alarm-service`
- `docker compose up -d alarm-service`

Путь B (если используется registry и image tags):

- зафиксировать `image: <repo>/<name>:<stable-tag>` в `docker-compose.yml`
- `docker compose pull alarm-service`
- `docker compose up -d alarm-service`
