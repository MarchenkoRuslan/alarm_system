# Agent Runbook (Operational, Polymarket MVP)

Операционный гайд для реализации и сопровождения в сеньорном минималистичном стиле.

## A. Quick start (10 минут)

1. Прочитать source-of-truth:
   - `verified-facts.md`
   - `adr/ADR-SET-v1.md`
   - `canonical-schema-versioning.md`
   - `rules-dsl-v1.md`
   - `mvp-scope-and-delivery-plan.md`
2. Определить затронутый контур:
   - ingestion / canonical / signal / rules / delivery / observability
3. Зафиксировать:
   - влияние на SLO;
   - риски корректности;
   - тесты и rollback.

## B. Runtime invariants

- Все события валидны по canonical schema.
- Дедуп/кулдаун channel-aware и deterministic.
- Каждое срабатывание содержит explainability.
- Для delayed-liquidity алертов: single-fire per `(alert_id, market_id)`.
- Hot path не делает блокирующие внешние API вызовы.
- `Alert` всегда связан с immutable `(rule_id, rule_version)`.

## C. Latency/SLO guardrails

- Primary KPI: `event_to_enqueue_ms` (p95 <= 1000ms).
- Measurement spec:
  - A: start at `position_update.event_ts`.
  - B: start at event that triggers 5m spike evaluation.
  - C: start at threshold crossing `liquidity_update.event_ts` (not `market_created`).
  - stop at durable enqueue/persist of `DeliveryPayload`.
- Если p95 > 1000ms:
  1. Проверить queue lag.
  2. Проверить hit-rate prefilter.
  3. Проверить время rule eval и Redis RTT.
  4. Выключить non-critical enrichment из hot path.

### Locked load profile for pre-prod gate

Использовать единый профиль для приемки перед продвижением фазы:

- sustained flow: `200 events/sec`;
- active alerts: `5000`;
- burst: `3x` на интервалах `60s`;
- reconnect storm: `3` принудительных transport-drop в `120s` + resubscribe + partial replay.

### Minimal signal metrics (MVP baseline)

Для пользовательских алертов по умолчанию поддерживаем только дешевые и доступные метрики:

- `price_return_1m_pct` (WS `last_trade_price` / `price_change`)
- `price_return_5m_pct` (WS `last_trade_price` / `price_change`)
- `spread_bps` (WS `book` best bid/ask)
- `book_imbalance_topN` (WS `book` depth)
- `liquidity_usd` (Gamma metadata sync)

Любые более сложные сигналы включаются только после профильных нагрузочных проверок.

### Default profile values (operator reference)

- conservative: `r1m>=2.0`, `r5m>=4.0`, `spread<=80bps`, `|imbalance|>=0.30`, `liquidity>=250k`, `cooldown=300s`
- balanced: `r1m>=1.2`, `r5m>=2.5`, `spread<=120bps`, `|imbalance|>=0.20`, `liquidity>=100k`, `cooldown=180s`
- aggressive: `r1m>=0.7`, `r5m>=1.5`, `spread<=180bps`, `|imbalance|>=0.12`, `liquidity>=50k`, `cooldown=90s`

Порядок безопасного тюнинга:

1. Менять один профиль/одну группу порогов за релиз.
2. Проверять `event_to_enqueue_ms`, trigger rate, dedup hit ratio.
3. При деградации возвращаться к предыдущему профилю без изменения кода.

## D. Backpressure actions

1. Queue lag warning:
   - ограничить worker concurrency ростом step-by-step;
   - включить batching там, где не ломает семантику.
2. Queue lag critical:
   - временно деградировать необязательные enrichments;
   - сохранить корректность trigger path как приоритет.
3. Recovery:
   - вернуть деградации только после стабилизации p95.
4. Saturation thresholds (обязательные):
   - warning: queue utilization >= 70%;
   - critical: queue utilization >= 90%;
   - recover: queue utilization < 70% в течение полного окна стабилизации.

## E. Checklists by change type

### E1. Schema changes

- [ ] Backward compatibility in `1.x`.
- [ ] Обновлены schema + Python contracts.
- [ ] Обновлен versioning policy.

### E2. Rule/DSL changes

- [ ] Обновлен `rules-dsl-v1.md`.
- [ ] Explainability не деградировала.
- [ ] Prefilter indexes покрывают новый rule path.
- [ ] Prefilter lifecycle не деградировал: index build выполняется на загрузке bindings, не на каждый event.
- [ ] Dedup/cooldown семантика сохранена.

### E3. Ingestion changes

- [ ] Heartbeat/reconnect/resubscribe tested.
- [ ] Category/tag mapping deterministic.
- [ ] Gamma sync не блокирует hot path.
- [ ] Для Example C / delayed-liquidity паттерна зафиксирована arm policy: WS `new_market` primary, Gamma discovery fallback.
- [ ] Assumption checks покрыты тестами: tag/category payload fields и liquidity semantics в metadata refresh path.

### E4. Delivery changes

- [ ] Новый канал: enum + provider + registry + binding migration.
- [ ] DeliveryAttempt пишет provider id/error/retry meta.
- [ ] Cooldown учитывает channel.
- [ ] Enqueue SLO не проседает.
- [ ] Trigger audit пишет `reason_json` и immutable `(rule_id, rule_version)` через `save_once` по `trigger_key`.
- [ ] Idempotent send проверен на повторном replay одного trigger window (между несколькими dispatcher instances).
- [ ] Cooldown source of truth — `alert.cooldown_seconds`.

### E5. State migration checks

- [ ] Redis dedup key формируется из deterministic trigger key.
- [ ] Redis cooldown key включает `channel`.
- [ ] Suppression/deferred watch state не теряет semantics one-shot и duration window.
- [ ] Crossing под suppression не помечает deferred watch как fired.
- [ ] Redis key TTL согласован с cooldown/bucket contracts.

## F. Minimal incident triage

1. **Symptom**: late alerts.
   - Check: ingest lag, queue lag, eval latency.
2. **Symptom**: duplicates.
   - Check: dedup key collisions/misses, cooldown key scope.
3. **Symptom**: missing alerts.
   - Check: prefilter false negatives, tag mapping drift, deferred watch state.
4. **Symptom**: reconnect storm.
   - Check: heartbeat cadence and resubscribe correctness.

## G. Rollback playbook

Rollback trigger conditions:

- p95 `event_to_enqueue_ms` remains above SLO after mitigation window.
- queue critical saturation persists despite backpressure actions.
- confirmed duplicate-send or missing-trigger incident on critical path.

Rollback steps:

1. Freeze non-critical enrichment and optional background jobs.
2. Roll back to last known stable release.
3. Reprocess checkpointed event window through replay path.
4. Validate parity and dedup/cooldown behavior before traffic restore.

## H. Smoke checks before merge

- No Kalshi references in runtime scope docs/contracts.
- Example preset tests (A/B/C-like) pass.
- Trigger explainability persisted.
- Channel abstraction intact (`Alert.channels`, `ChannelBinding`, `DeliveryProvider`).
- p95 enqueue latency budget verified on synthetic burst.
- Backpressure tests pass for warning/critical/recovery saturation states.
- Compute/rules baseline still green (`pytest tests/compute tests/rules`) before state/delivery merge.

## I. Pre-hardening checklist

- [x] Metrics wired and checked in CI smoke:
  - `event_to_enqueue_ms`
  - `rule_eval_ms`
  - `queue_lag_ms`
  - `dedup_hits_total`
- [x] Replay smoke for idempotency:
  - repeated trigger window does not duplicate channel sends;
  - `trigger_audit` remains `save_once` by `trigger_key`.
- [x] Retry/failure audit smoke:
  - all retry attempts persisted with `RETRYING`;
  - terminal attempt persisted with `SENT` or `FAILED`.
- [x] Load-profile dry run completed:
  - baseline `200 events/sec`;
  - burst `3x` for `60s`;
  - reconnect storm shape from scope lock verified.
- [x] Ready-for-backpressure criteria:
  - warning/critical/recovery thresholds configured (`70%/90%/<70% window`);
  - non-critical degradation switches documented and testable.

## J. Hardening verification commands

Use these commands as release-gate smoke evidence:

1. `pytest tests/test_runtime_metrics.py tests/test_observability.py`
2. `pytest tests/test_backpressure_runtime.py`
3. `pytest tests/test_load_harness.py`
4. `pytest tests/ingestion/test_polymarket_reconnect.py`
5. `pytest tests/test_rollback_drill.py`

Operational helpers for pre-prod checks:

- Smoke locked profile (compressed CI windows): `run-load-gate --profile smoke`
- Contract long burst profile (`3x` for `60s`): `run-load-gate --profile long --max-runtime-sec 900 --progress-every-events 2000`
- Rollback drill smoke: `run-rollback-gate`

Long burst pass criteria:

- `run-load-gate --profile long --max-runtime-sec <budget>` exits with code `0`.
- JSON output has `"slo":{"passed":true}` and `p95_ms <= 1000`.
- During long run, progress logs appear every configured batch and are used for hang diagnostics.
- `run-rollback-gate` exits with code `0` and `"passed":true`.

## K. Docker Compose deployment runbook (single-host MVP)

### K1. Required runtime config

- Copy `.env.example` to `.env`.
- Fill mandatory values:
  - `ALARM_ASSET_IDS`
  - `ALARM_TELEGRAM_BOT_TOKEN` (required for live mode)
- Runtime config files:
  - `deploy/config/rules.sample.json`
  - `deploy/config/alerts.sample.json`
  - `deploy/config/channel-bindings.sample.json`

### K2. Startup sequence

Preflight before startup:

- `docker compose --profile dry-run config` passes.
- `.env` has valid `ALARM_ASSET_IDS` and `ALARM_TELEGRAM_BOT_TOKEN`.
- runtime config JSON files are aligned by `rule_id + rule_version`.

1. Dry-run precheck:
   - `docker compose --profile dry-run up --build alarm-service-dry-run redis`
2. Verify logs:
   - startup checks report Redis connectivity as `ok`;
   - startup mode is `dry_run`;
   - progress and metrics snapshot logs are emitted;
   - no fatal reconnect loop.
3. Stop dry-run and start live:
   - `docker compose up --build -d redis alarm-service`

### K3. Go/No-Go for first production enable

Go only when all checks are true:

- `run-load-gate --profile long --target-p95-ms 1000` passed.
- `run-rollback-gate` passed.
- replay path has no duplicate channel sends.
- runtime logs show stable `event_to_enqueue_ms` within SLO.

No-Go if any condition fails; perform rollback sequence from section G.

### K4. Rollback-to-previous-image procedure

Hybrid rollback modes:

1. Build-only mode (default local single-host):
   - `git checkout <stable-tag>`
   - `docker compose build alarm-service`
   - `docker compose up -d alarm-service`
2. Registry image-tag mode (optional):
   - set `image: <repo>/<name>:<stable-tag>` in compose
   - `docker compose pull alarm-service`
   - `docker compose up -d alarm-service`
3. For both modes:
   - run `run-rollback-gate`;
   - run replay parity checks before restoring full traffic.
