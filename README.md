# alarm_system

MVP-система кастомных алертов для prediction markets (текущий scope: только Polymarket).

Проект закладывает контрактную и архитектурную основу для:
- realtime ingest рыночных событий;
- нормализации в канонический формат;
- вычисления сигналов и оценки правил (DSL);
- dedup/cooldown и explainability;
- channel-agnostic доставки (MVP провайдер: Telegram).

## Текущий статус

Репозиторий сейчас содержит в первую очередь архитектурные артефакты и базовые runtime-контракты (модели/интерфейсы), на которых строится реализация.

## Что уже есть

- Canonical event contract:
  - `schemas/canonical_event.v1.schema.json`
  - `src/alarm_system/canonical_event.py`
- Rule DSL contract:
  - `src/alarm_system/rules_dsl.py`
  - `docs/architecture/rules-dsl-v1.md`
- Dedup/cooldown helpers:
  - `src/alarm_system/dedup.py`
- Domain entities:
  - `src/alarm_system/entities.py`
- Delivery abstraction:
  - `src/alarm_system/delivery.py`
- Source adapter abstraction:
  - `src/alarm_system/adapters.py`
- Ingestion phase-1 runtime:
  - `src/alarm_system/ingestion/`
  - `src/alarm_system/ingestion/polymarket/`

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
