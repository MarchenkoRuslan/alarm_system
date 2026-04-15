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

```
src/alarm_system/
├── __init__.py                  # публичные контракты пакета
├── canonical_event.py           # CanonicalEvent, build_event_id, build_payload_hash
├── adapters.py                  # MarketAdapter, AdapterRegistry
├── rules_dsl.py                 # DSL v1, trigger keys, cooldown
├── dedup.py                     # deterministic dedup/cooldown keys
├── entities.py                  # User, Alert, Market, Trade и др.
├── delivery.py                  # DeliveryPayload, DeliveryProvider, ProviderRegistry
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

## MVP boundaries

- Рынок: только Polymarket.
- SLA: p95 `source_event_ts -> delivery_enqueue_ts <= 1s`.
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
