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
- `docs/architecture/ingestion-phase1-implementation-notes.md`
- `docs/architecture/phase2-exit-baseline.md`
- `docs/architecture/phase3-entry-design.md`
- `docs/architecture/phase4-gap-matrix.md`

## MVP boundaries

- Рынок: только Polymarket.
- SLA: p95 `source_event_ts -> delivery_enqueue_ts <= 1s`.
- Phase 2 baseline повторно подтвержден: `pytest tests/compute tests/rules`.
- Phase 4 gate smoke (SLO/backpressure/reconnect/rollback):
  - `pytest tests/test_phase4_metrics.py tests/test_observability.py tests/test_backpressure_runtime.py tests/test_phase4_load_harness.py tests/ingestion/test_polymarket_reconnect.py tests/test_rollback_drill.py`
- Phase 4 operational commands:
  - `run-phase4-load --profile smoke`
  - `run-phase4-load --profile long --max-runtime-sec 900 --progress-every-events 2000`
  - `run-phase4-rollback`
  - CI/manual job: `.github/workflows/phase4-long-burst.yml`
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
