# Phase 4 Gap Matrix (2026-04-16)

Цель: зафиксировать разницу между формальным Phase 4 gate (test baseline) и hardening-уровнем, который нужен перед production rollout.

## Матрица соответствия

| Критерий | Текущее покрытие | Пробел | Дальнейшее действие |
| --- | --- | --- | --- |
| p95 `event_to_enqueue_ms` на locked profile | `tests/test_phase4_load_harness.py` + `src/alarm_system/load_harness.py` | До этого проверялся в основном delivery-only smoke, без полного runtime-пути | Перевести smoke по умолчанию на сквозной путь `canonical -> RuleRuntime -> DeliveryDispatcher` |
| Reconnect storm без дубликатов | `tests/ingestion/test_polymarket_reconnect.py` | Нет прямого объединения reconnect + full load в одном сценарии | Оставить как отдельный gate и добавить correlation через единый runbook |
| Backpressure warning/critical/recovery | `tests/test_backpressure_runtime.py` + `src/alarm_system/backpressure.py` | Покрытие есть, но отдельно от long burst-профиля | Сохранить acceptance тесты и дополнить операционным long-burst прогоном |
| Rollback drill smoke | `tests/test_rollback_drill.py` + `src/alarm_system/rollback_drill.py` | Нет отдельной CLI-команды для оперативного запуска | Добавить `run-phase4-rollback` и включить в инструкции |
| Metric catalog labels | `src/alarm_system/observability.py`, `tests/test_phase4_metrics.py` | Не все runtime-метрики соответствовали минимальному каталогу | Добавить лейблы для `event_to_enqueue_ms`, `queue_lag_ms`, `rule_eval_ms`, `dedup_hits_total`; добавить `prefilter_hit_ratio` и `ingest_lag_ms` в runtime |
| Locked profile burst `3x` for `60s` | Контракт в `mvp-scope-and-delivery-plan.md` | CI smoke использует сжатые окна для скорости | Ввести отдельный long profile и запуск через отдельную команду/джоб |

## Итоговый статус

- **Phase 4 gate (test baseline):** выполнен.
- **Hardening continuation (Phase 4.1):** ключевые стабилизационные фиксы внедрены.

## Stabilization closeout (2026-04-16)

- long-profile получил runtime guardrail (`--max-runtime-sec`) и progress-диагностику (`--progress-every-events`).
- E2E smoke сделал более репрезентативный enqueue path (`dispatched_queued` в smoke около `80%` от входного потока вместо прежних ~`15%`).
- rollback CLI теперь сериализует явный флаг `passed` в JSON.
- dispatch-only path проверяет инварианты консистентности `decision.rule_id/rule_version == alert.rule_id/rule_version`.
- Остаточный риск: фактическое wall-clock время строгого long-profile зависит от host perf; runtime guardrail обязателен в pre-prod job.
