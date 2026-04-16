# Hardening Gap Matrix (2026-04-16)

Goal: capture the gap between the formal test gate and hardening level before production rollout.

## Alignment matrix

| Criterion | Current coverage | Gap | Next action |
| --- | --- | --- | --- |
| p95 `event_to_enqueue_ms` on locked profile | `tests/test_load_harness.py` + `src/alarm_system/load_harness.py` | Previously validated mostly on delivery-only path | Use end-to-end path `canonical -> RuleRuntime -> DeliveryDispatcher` as smoke default |
| Reconnect storm without duplicates | `tests/ingestion/test_polymarket_reconnect.py` | No unified reconnect + full-load scenario | Keep a separate gate and link it to a single runbook |
| Backpressure warning/critical/recovery | `tests/test_backpressure_runtime.py` + `src/alarm_system/backpressure.py` | Coverage is separate from long burst | Keep acceptance tests and supplement with operational long-burst run |
| Rollback drill smoke | `tests/test_rollback_drill.py` + `src/alarm_system/rollback_drill.py` | Need convenient CLI for manual execution | Use `run-rollback-gate` in runbook and CI |
| Metric catalog labels | `src/alarm_system/observability.py`, `tests/test_runtime_metrics.py` | Runtime metrics needed to be aligned with catalog | Keep labels for `event_to_enqueue_ms`, `queue_lag_ms`, `rule_eval_ms`, `dedup_hits_total`, `prefilter_hit_ratio`, `ingest_lag_ms` |
| Locked profile burst `3x` for `60s` | Contract in `mvp-scope-and-delivery-plan.md` | CI smoke uses compressed windows | Use a dedicated long profile and separate workflow gate |

## Final status

- Gate baseline: completed.
- Hardening continuation: key stabilization fixes are implemented.
