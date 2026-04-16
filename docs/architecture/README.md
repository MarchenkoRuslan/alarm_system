# Polymarket Alerts Architecture Pack

Каталог содержит актуальные архитектурные документы для MVP (только Polymarket).

## Доменная карта

- `ingestion` -> приём и нормализация внешних событий.
- `canonical` -> контракт события и правила версионирования схемы.
- `compute` -> вычисление сигналов и prefilter-кандидатов.
- `rules` -> оценка DSL, suppression, deferred-watch, explainability.
- `delivery` -> аудит trigger, cooldown/idempotency, отправка через provider abstraction.
- `state` -> in-memory/Redis state stores для dedup/cooldown/suppression/deferred-watch.
- `observability` -> SLO и runtime-метрики.

## Глоссарий

- `rule` - логика срабатывания (`rule_id`, `rule_version`, expression, filters).
- `alert` - пользовательская подписка на конкретную версию правила (`alert_id`).
- `trigger` - факт срабатывания правила для scope с explainability (`reason_json`).
- `scope` - область дедупликации/кулдауна, обычно `market_id`.
- `prefilter` - предварительный отбор кандидатов по недорогим индексам.

## Source Of Truth

- `verified-facts.md` - подтверждённые внешние ограничения Polymarket.
- `adr/ADR-SET-v1.md` - принятые архитектурные решения.
- `canonical-schema-versioning.md` - политика версионирования schema/контрактов.
- `rules-dsl-v1.md` - DSL контракт, explainability, dedup/cooldown semantics.
- `mvp-scope-and-delivery-plan.md` - scope и delivery-подход в доменных терминах.
- `implementation-blueprint.md` - прикладная карта модулей и потоков.
- `agent-runbook.md` - операционные проверки и runbook.

## Runtime Anchors

- `../../src/alarm_system/schemas/canonical_event.v1.schema.json`
- `../../src/alarm_system/canonical_event.py`
- `../../src/alarm_system/rules_dsl.py`
- `../../src/alarm_system/dedup.py`
- `../../src/alarm_system/entities.py`
- `../../src/alarm_system/delivery.py`
- `../../src/alarm_system/adapters.py`
