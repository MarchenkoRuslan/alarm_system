# Agent Runbook (Operational)

Операционный гайд: как агенту быстро разобраться и безопасно вносить изменения.

## A. Быстрый старт за 10 минут

1. Прочитать:
   - `verified-facts.md`
   - `adr/ADR-SET-v1.md`
   - `canonical-schema-versioning.md`
   - `rules-dsl-v1.md`
2. Проверить текущую задачу:
   - это MVP или V2+?
   - затрагивает schema/DSL или только реализацию?
3. Перед изменениями зафиксировать:
   - предполагаемый риск,
   - affected components,
   - проверку успеха.

## B. Карта системы

1. Ingestion adapters  
   Источники: Polymarket WS/Gamma, Kalshi WS/Historical, Polymarket on-chain.
2. Canonical normalizer  
   Все source payload приводятся к `canonical_event.v1`.
3. Signal compute  
   Вычисление сигналов в окнах, состояние в Redis.
4. Rule engine  
   DSL оценка, reason/explainability, trigger generation.
5. Delivery  
   Каналы уведомлений, dedup/cooldown/suppression.
6. Observability  
   Метрики, трассировка, аудит.

## C. Обязательные инварианты

- Любой event валиден относительно canonical schema.
- Каждый trigger имеет deterministic key.
- Любая отправка уведомления трассируется до source event.
- При повторной обработке истории результат детерминирован.
- Изменения правил не ломают старые `rule_version`.

## D. Checklist по типу изменений

### D1. Если меняется schema
- [ ] Поднять версию по правилам semver.
- [ ] Обновить policy документ.
- [ ] Добавить migration notes.
- [ ] Обеспечить backward compatibility или dual-write.

### D2. Если меняется DSL/rule semantics
- [ ] Описать новую семантику в `rules-dsl-v1.md`.
- [ ] Гарантировать explainability.
- [ ] Проверить dedup/cooldown совместимость.

### D3. Если меняется ingestion
- [ ] Проверить heartbeat/reconnect.
- [ ] Проверить checkpointing/resume.
- [ ] Документировать source limits/rate constraints.

### D4. Если меняется delivery
- [ ] Не нарушить suppression/cooldown.
- [ ] Сохранить audit trail.
- [ ] Проверить retry/backoff.

## E. Минимальный шаблон отчета агента

1. Что изменено (файлы и слой системы)
2. Почему это решение (ссылка на ADR/ограничение)
3. Какие риски закрыты
4. Что осталось вне scope
5. Как проверить результат

## F. Границы MVP (коротко)

В MVP обязательно:
- базовые сигналы: VolumeSpike, ProbabilityJump, LargeTrade, FollowWallet, EventMomentum
- DSL v1
- dedup/cooldown/suppression
- webhook/Telegram/email
- наблюдаемость и SLO

Вне MVP:
- сложные кросс-рыночные количественные модели и multi-region.
