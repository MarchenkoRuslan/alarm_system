# Agent Runbook (Operational, Polymarket MVP)

Operational guide for implementation and maintenance in a senior minimalistic style.

## A. Quick start (10 minutes)

1. Read source-of-truth:
   - `verified-facts.md`
   - `adr/ADR-SET-v1.md`
   - `canonical-schema-versioning.md`
   - `rules-dsl-v1.md`
   - `mvp-scope-and-delivery-plan.md`
2. Determine affected domain:
   - ingestion / canonical / signal / rules / delivery / observability
3. Record:
   - SLO impact;
   - correctness risks;
   - tests and rollback.

## A1. App/core ownership map

Keep one repository with two logical apps and one shared core:

- `alarm_system.apps.api`: API startup wiring and deployment-facing entrypoint.
- `alarm_system.apps.worker`: worker startup wiring and deployment-facing entrypoint.
- `src/alarm_system/*`: shared contracts and domain runtime (core).

Naming convention for new modules:

- API-only runtime wiring: `alarm_system/apps/api/*` (or `alarm_system/api/*` if shared package access is required).
- Worker-only runtime wiring: `alarm_system/apps/worker/*`.
- Shared logic and data contracts: `alarm_system/*`.

Do not duplicate domain models/rules across app folders; keep a single core source.

## B. Runtime invariants

- All events are valid against canonical schema.
- Dedup/cooldown is channel-aware and deterministic.
- Every trigger includes explainability.
- For delayed-liquidity alerts: single-fire per `(alert_id, market_id)`.
- Hot path does not make blocking external API calls.
- `Alert` is always bound to immutable `(rule_id, rule_version)`.

## C. Latency/SLO guardrails

- Primary KPI: `event_to_enqueue_ms` (p95 <= 1000ms).
- Measurement spec:
  - A: start at `position_update.event_ts`.
  - B: start at event that triggers 5m spike evaluation.
  - C: start at threshold crossing `liquidity_update.event_ts` (not `market_created`).
  - stop at durable enqueue/persist of `DeliveryPayload`.
- If p95 > 1000ms:
  1. Check queue lag.
  2. Check prefilter hit rate.
  3. Check rule-eval time and Redis RTT.
  4. Disable non-critical enrichments in hot path.

### Locked load profile for pre-prod gate

Use a single profile for acceptance before phase promotion:

- sustained flow: `200 events/sec`;
- active alerts: `5000`;
- burst: `3x` during `60s` intervals;
- reconnect storm: `3` forced transport drops in `120s` + resubscribe + partial replay.

### Minimal signal metrics (MVP baseline)

For user alerts by default, support only cheap and available metrics:

- `price_return_1m_pct` (WS `last_trade_price` / `price_change`)
- `price_return_5m_pct` (WS `last_trade_price` / `price_change`)
- `spread_bps` (WS `book` best bid/ask)
- `book_imbalance_topN` (WS `book` depth)
- `liquidity_usd` (Gamma metadata sync)

Any more complex signals are enabled only after dedicated load validation.

### Default profile values (operator reference)

- conservative: `r1m>=2.0`, `r5m>=4.0`, `spread<=80bps`, `|imbalance|>=0.30`, `liquidity>=250k`, `cooldown=300s`
- balanced: `r1m>=1.2`, `r5m>=2.5`, `spread<=120bps`, `|imbalance|>=0.20`, `liquidity>=100k`, `cooldown=180s`
- aggressive: `r1m>=0.7`, `r5m>=1.5`, `spread<=180bps`, `|imbalance|>=0.12`, `liquidity>=50k`, `cooldown=90s`

Safe tuning order:

1. Change one profile/one threshold group per release.
2. Validate `event_to_enqueue_ms`, trigger rate, dedup hit ratio.
3. If degraded, revert to previous profile without code changes.

## D. Backpressure actions

1. Queue lag warning:
   - limit worker concurrency growth step by step;
   - enable batching where semantics stay intact.
2. Queue lag critical:
   - temporarily degrade non-critical enrichments;
   - preserve trigger-path correctness as priority.
3. Recovery:
   - rollback degradations only after p95 stabilizes.
4. Saturation thresholds (mandatory):
   - warning: queue utilization >= 70%;
   - critical: queue utilization >= 90%;
   - recover: queue utilization < 70% for a full stabilization window.

## E. Checklists by change type

### E1. Schema changes

- [ ] Backward compatibility in `1.x`.
- [ ] Schema + Python contracts updated.
- [ ] Versioning policy updated.

### E2. Rule/DSL changes

- [ ] `rules-dsl-v1.md` updated.
- [ ] Explainability is not degraded.
- [ ] Prefilter indexes cover the new rule path.
- [ ] Prefilter lifecycle is not degraded: index build runs on bindings load, not per event.
- [ ] Dedup/cooldown semantics preserved.

### E3. Ingestion changes

- [ ] Heartbeat/reconnect/resubscribe tested.
- [ ] Category/tag mapping deterministic.
- [ ] Gamma sync does not block hot path.
- [ ] For Example C / delayed-liquidity pattern, arm policy is fixed: WS `new_market` primary, Gamma discovery fallback.
- [ ] Assumption checks are covered by tests: tag/category payload fields and liquidity semantics in metadata refresh path.

### E4. Delivery changes

- [ ] New channel: enum + provider + registry + binding migration.
- [ ] DeliveryAttempt writes provider id/error/retry metadata.
- [ ] Cooldown accounts for channel.
- [ ] Enqueue SLO does not regress.
- [ ] Trigger audit writes `reason_json` and immutable `(rule_id, rule_version)` via `save_once` by `trigger_key`.
- [ ] Idempotent send verified on repeated replay of one trigger window (across multiple dispatcher instances).
- [ ] Cooldown source of truth is `alert.cooldown_seconds`.

### E5. State migration checks

- [ ] Redis dedup key is built from deterministic trigger key.
- [ ] Redis cooldown key includes `channel`.
- [ ] Suppression/deferred watch state preserves one-shot and duration-window semantics.
- [ ] Crossing under suppression does not mark deferred watch as fired.
- [ ] Redis key TTL aligns with cooldown/bucket contracts.

### E6. Interactive Telegram UI changes

- [ ] New callbacks register via `_callbacks._HANDLERS` (or the
      wizard action set), not ad-hoc router logic.
- [ ] Every `callback_data` fits in 64 bytes; long payloads go
      through `SessionStore` with short tokens.
- [ ] `setMyCommands` still exposes only the short entry-point list
      (`/start`, `/alerts`, `/new`, ...); advanced commands stay
      `hidden=True` in `COMMAND_CATALOG`.
- [ ] Wizard finalisation goes through `_create_from_payload` so
      rule-identity whitelist and ownership checks apply.
- [ ] `SessionStore` key prefix and TTL aligned: `alarm:session:*`
      with 10 min default; no persistent data there.
- [ ] Slash-command behaviour preserved (contract tests in
      `tests/test_telegram_command_catalog_contract.py`).

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

1. Freeze non-critical enrichments and optional background jobs.
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

Operational helpers for pre-production checks:

- Smoke locked profile (compressed CI windows): `run-load-gate --profile smoke`
- Contract long burst profile (`3x` for `60s`): `run-load-gate --profile long --max-runtime-sec 900 --progress-every-events 2000`
- Rollback drill smoke: `run-rollback-gate`

Long burst pass criteria:

- `run-load-gate --profile long --max-runtime-sec <budget>` exits with code `0`.
- JSON output has `"slo":{"passed":true}` and `p95_ms <= 1000`.
- During long run, progress logs appear every configured batch and are used for hang diagnostics.
- `run-rollback-gate` exits with code `0` and `"passed":true`.

## K. Docker Compose deployment runbook (single-host MVP)

Railway split (same repository, two services):

- API service: build from `Dockerfile.api`, command `run-api`, public domain enabled.
- Worker service: build from `Dockerfile.worker`, command `run-worker`, no public domain.

### K1. Required runtime config

- Copy `.env.example` to `.env`.
- Fill mandatory values:
  - `ALARM_ASSET_IDS`
  - `ALARM_TELEGRAM_BOT_TOKEN` (required for live mode)
- Runtime config files:
  - `deploy/config/rules.sample.json`
  - `deploy/config/alerts.sample.json`
  - `deploy/config/channel-bindings.sample.json`
  - note: sample alerts in `alerts.sample.json` are enabled by default;
    set `"enabled": false` for entries you do not want to fire during
    bootstrap validation

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
