# AGENTS Guide

Практическое руководство для AI-агентов и разработчиков по проекту `alarm_system`.

## 1) Цель проекта

Система кастомных алертов для prediction markets, scoped только на Polymarket:

- ingest рыночных и при необходимости on-chain сигналов Polymarket,
- нормализация в канонический формат,
- вычисление сигналов,
- оценка правил (DSL),
- отправка уведомлений через абстракцию каналов (MVP провайдер: Telegram) с explainability.

## 2) Где что лежит

- Архитектурные документы: `docs/architecture/`
- Canonical schema: `schemas/canonical_event.v1.schema.json`
- Python-модели событий: `src/alarm_system/canonical_event.py`
- Python-модели DSL: `src/alarm_system/rules_dsl.py`
- Dedup/cooldown helpers: `src/alarm_system/dedup.py`

## 3) Source of truth (читать в этом порядке)

1. `docs/architecture/verified-facts.md`  
   Подтвержденные внешние ограничения API/WS/on-chain.
2. `docs/architecture/adr/ADR-SET-v1.md`  
   Принятые архитектурные решения.
3. `docs/architecture/canonical-schema-versioning.md`  
   Правила версионирования контрактов.
4. `docs/architecture/rules-dsl-v1.md`  
   Контракт правил, dedup/cooldown, explainability.
5. `docs/architecture/mvp-scope-and-delivery-plan.md`  
   Границы MVP и delivery-план.

## 4) Непереговорные правила для агента

1. Не ломать контракт canonical schema без versioning-процедуры.
2. Любая интеграция с внешним источником должна быть подтверждена в docs и verified links.
3. Rule changes только через версионирование (`rule_version` immutable).
4. Обязательно сохранять explainability (`reason_json`) для каждого trigger.
5. Дубликаты уведомлений блокируются deterministic trigger key.
6. Любые fallback/assumptions документируются явно.
7. SLA для MVP: `source_event_ts -> delivery_enqueue_ts <= 1s` (p95).

## 5) Стандартный workflow агента

1. Прочитать source-of-truth документы.
2. Определить, к какому слою относится задача:
   - ingestion
   - canonical normalization
   - signal compute
   - rules engine
   - delivery
3. Проверить, затрагивается ли контракт schema/DSL.
4. Внести изменения минимально в нужный слой.
5. Обновить релевантную документацию в `docs/architecture/`.
6. Прогнать проверки (линтер/тесты, если есть).
7. Зафиксировать риски и влияние на MVP scope.

## 6) Definition of done для любых изменений

- Изменение согласовано с ADR-подходом.
- Документация не расходится с кодом.
- Нет регресса в dedup/cooldown/explainability.
- Ясно описано: что сделано, зачем, и как проверить.

## 7) Быстрые сценарии

### Добавить новый источник рынка

- Для текущего production scope активен только Polymarket.
- Расширяемость закладывается через adapter boundary, но включение нового рынка только после ADR + контрактных тестов + SLO ревалидации.

### Добавить новый сигнал

- Описать формулу и окно.
- Добавить вычисление в compute слой.
- Обновить `rules-dsl-v1.md` (если новые операторы/семантика).
- Проверить dedup/cooldown поведение.

### Изменить логику уведомлений

- Сохранить ключевые инварианты: no-dup, cooldown, suppression.
- Не удалять explainability из payload.
- В hot path не делать channel-specific логику: только `DeliveryPayload` + provider registry.
- Обновить runbook и acceptance criteria при необходимости.

## 8) Что не делать

- Не менять plan-файлы как источник реализации.
- Не внедрять новые обязательные технологии без отдельного решения.
- Не добавлять “умную” магию без явного описания в документации.
- Не возвращать Kalshi/multi-source в MVP без отдельного продуктового решения.

## 9) Контакт между человеком и агентом

Рекомендуемый формат задач для агента:

- Контекст: какой слой меняем
- Цель: что должно появиться
- Ограничения: что нельзя ломать
- Критерии приемки: как понять, что готово

Если данных не хватает, агент сначала задает уточняющие вопросы, затем предлагает короткий план и только потом меняет код.
