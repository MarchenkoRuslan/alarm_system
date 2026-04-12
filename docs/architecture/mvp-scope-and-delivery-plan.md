# MVP Scope Lock and 6-Week Delivery Plan

## MVP scope lock

## In scope (must deliver)

1. Source adapters:
   - Polymarket websocket market ingestion
   - Polymarket Gamma metadata enrichment
   - Kalshi websocket ticker/trade/lifecycle ingestion
   - Kalshi historical backfill (cutoff-aware)
2. Canonical event pipeline:
   - schema validation v1
   - checkpointing and replay-safe ingestion
3. Signals:
   - `VolumeSpike`
   - `ProbabilityJump`
   - `LargeTrade`
   - `FollowWallet` (Polymarket on-chain where available; API fallback)
   - `EventMomentum`
4. Rules:
   - DSL v1 (`AND/OR/NOT`, thresholds, windows)
   - rule versioning
   - explainability payload (`reason_json`)
5. Notifications:
   - webhook, Telegram, email
   - dedup + cooldown + suppression
6. Observability:
   - ingest lag, eval latency, delivery success, dedup hit rate
   - trace correlation from canonical event to delivery attempt

## Out of scope (defer)

- Advanced quantitative signals (correlation breakdown, sequence mining)
- Social/comments sentiment ingestion
- Complex portfolio-level optimization recommendations
- Full multi-region deployment

## Acceptance criteria (MVP)

1. 95% realtime triggers delivered within 5 seconds after source event ingest.
2. Duplicate deliveries for same `(tenant, rule, scope, bucket)` are blocked.
3. Rule explainability is visible in persisted trigger reason.
4. Backfill run can replay at least 7 days of historical data without pipeline corruption.
5. Adapter outage does not stop other source pipelines.

## Six-week delivery plan

### Week 1: Foundations
- Finalize canonical schema v1 and contract tests.
- Create adapter skeletons and checkpoint tables.
- Set up baseline observability dashboards.

### Week 2: Source ingestion
- Implement Polymarket WS + Gamma mapper.
- Implement Kalshi WS mapper.
- Add heartbeat/reconnect/supervisor loops.

### Week 3: Signal compute (batch 1)
- Implement window state management in Redis.
- Deliver `VolumeSpike`, `ProbabilityJump`, `LargeTrade`.
- Validate latency and lag under synthetic load.

### Week 4: Rules engine + signals (batch 2)
- Implement DSL evaluator v1.
- Add rule versioning and explainability payload.
- Add deterministic trigger key and dedup path.
- Deliver `FollowWallet` (on-chain adapter + API fallback) and `EventMomentum` (Gamma cross-market grouping).

### Week 5: Delivery orchestration
- Implement webhook/Telegram/email channels.
- Add cooldown and suppression orchestration.
- Persist delivery attempts and failures with retry policy.

### Week 6: Hardening and backfill
- Implement Kalshi historical replay flow.
- Run failure scenario tests (source down, retry storms, duplicate streams).
- Tune SLO dashboards, define runbook, lock release checklist.

## Exit checklist before MVP release

- Contract tests green for all adapters.
- Replay parity check passed for a selected 24h window.
- Load test passed at target event rate.
- On-call runbook reviewed.
- Product sign-off on default thresholds and noise controls.
