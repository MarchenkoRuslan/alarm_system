# Hardening Gap Matrix (2026-04-16)

Цель: зафиксировать разницу между формальным test gate и hardening-уровнем перед production rollout.

## Матрица соответствия

| Критерий | Текущее покрытие | Пробел | Дальнейшее действие |
| --- | --- | --- | --- |
| p95 `event_to_enqueue_ms` на locked profile | `tests/test_load_harness.py` + `src/alarm_system/load_harness.py` | Ранее проверялся преимущественно delivery-only path | Использовать сквозной путь `canonical -> RuleRuntime -> DeliveryDispatcher` как smoke default |
| Reconnect storm без дубликатов | `tests/ingestion/test_polymarket_reconnect.py` | Нет единого сценария reconnect + full load | Сохранять отдельный gate и связать его с единым runbook |
| Backpressure warning/critical/recovery | `tests/test_backpressure_runtime.py` + `src/alarm_system/backpressure.py` | Покрытие отдельно от long burst | Сохранить acceptance tests и дополнять операционным long-burst прогоном |
| Rollback drill smoke | `tests/test_rollback_drill.py` + `src/alarm_system/rollback_drill.py` | Нужно удобное CLI для ручного запуска | Использовать `run-rollback-gate` в runbook и CI |
| Metric catalog labels | `src/alarm_system/observability.py`, `tests/test_runtime_metrics.py` | Требовалось доведение runtime-метрик до каталога | Поддерживать лейблы для `event_to_enqueue_ms`, `queue_lag_ms`, `rule_eval_ms`, `dedup_hits_total`, `prefilter_hit_ratio`, `ingest_lag_ms` |
| Locked profile burst `3x` for `60s` | Контракт в `mvp-scope-and-delivery-plan.md` | CI smoke использует сжатые окна | Использовать отдельный long profile и отдельный workflow gate |

## Итоговый статус

- Gate baseline: выполнен.
- Hardening continuation: ключевые стабилизационные фиксы внедрены.
