# ADR Set v1: Polymarket Alerts MVP

Status: Accepted  
Date: 2026-04-14

## ADR-0001: Polymarket-only source for MVP
- Context: Need fast delivery and low integration risk.
- Decision: MVP integrates only Polymarket (WS + Gamma).
- Consequence: Faster execution; multi-source deferred.

## ADR-0002: Stream-first low-latency path
- Context: SLO is <= 1s to enqueue.
- Decision: WebSocket first, minimal hot path, no blocking enrichment calls.
- Consequence: Better latency, stricter runtime discipline.

## ADR-0003: Canonical event boundary
- Context: WS/Gamma payloads are source-specific.
- Decision: Normalize everything into canonical v1 before compute/rules/delivery.
- Consequence: Stable contracts and replayability.

## ADR-0004: Hybrid state model (Redis + Postgres)
- Context: Need both low-latency state and durable state.
- Decision: Redis for windows/dedup/cooldown/hot indexes; Postgres for durable domain state.
- Consequence: Performance with crash-safe recovery.
- Interactive addendum: alert/binding CRUD truth is stored in Postgres, while Redis
  is used as runtime read cache for active alert configuration snapshots.
- Contract addendum: alert writes use explicit create/update paths with optimistic
  version checks to prevent implicit last-write-wins updates.
- Runtime policy addendum: in-memory config store fallback is restricted to
  dev/test; staging/prod fail-fast without Postgres DSN.
- Migration addendum: current MVP applies SQL migrations at API startup;
  target state is Alembic versioned migrations with explicit revision control.

## ADR-0005: Immutable rule versions + explainability
- Context: Auditability and deterministic replay are mandatory.
- Decision: Trigger references exact `(rule_id, rule_version)` and stores `reason_json`.
- Consequence: Reliable postmortems and safe rule evolution.

## ADR-0006: At-least-once ingestion with idempotent triggers
- Context: WS reconnect/retry can duplicate events.
- Decision: Accept at-least-once; dedup with deterministic trigger keys and channel-aware cooldown.
- Consequence: Simpler reliability model with explicit idempotency.

## ADR-0007: Staged rule evaluation
- Context: Full DSL evaluation for each event is expensive at scale.
- Decision: Coarse prefilter `(rule_type, tag, event_type)` before predicate evaluation.
- Consequence: Lower CPU and lower latency under burst load.

## ADR-0008: Deferred watch for delayed-liquidity semantics
- Context: Delayed-liquidity alert patterns (e.g. Example C) can trigger hours or days after market creation.
- Decision: Arm deferred watch on `market_created`; fire once on first threshold crossing.
- Current implementation addendum: deferred-watch state is Redis-backed with TTL and replay-safe semantics.
- Consequence: Correct business semantics and replay-safe behavior; Postgres-backed watch durability is future roadmap.

## ADR-0009: Channel-abstracted delivery
- Context: MVP provider is Telegram, but future channels must be low-cost to add.
- Decision: Use `DeliveryProvider` ABC + `ProviderRegistry`, `ChannelBinding`, `Alert.channels`.
- Consequence: Add-channel changes stay localized to provider and config.

## ADR-0010: SLO-first observability
- Context: Without strict latency KPIs, regressions are discovered too late.
- Decision: Primary KPI `event_to_enqueue_ms` (p95 <= 1000ms) + queue lag + eval latency.
- Consequence: Clear runtime health signals and faster incident response.

## ADR-0011: Latency budget and backpressure are first-class
- Context: Throughput spikes can violate SLO and overload workers.
- Decision: Define per-stage latency budgets and bounded queues with explicit backpressure actions.
- Consequence: Graceful degradation under load without correctness loss.

## ADR-0012: Keep architecture minimal until proven needed
- Context: Overengineering slows delivery and increases failure surface.
- Decision: Build smallest set of modules that satisfy initial reference presets and SLO, then expand by profiling data.
- Consequence: Faster MVP with clean extension points and fewer moving parts.

## ADR-0013: Gamma HTTP sync + serialized worker evaluation
- Context: Gamma metadata (`GET /markets` by tag) must stay off the WebSocket hot path, but operators need fresh catalog snapshots without restarting the worker. Concurrent WS and Gamma batches must not call `RuleRuntime` at the same time.
- Decision: Run `poll_once` at worker bootstrap when tags are configured; optionally repeat on `ALARM_GAMMA_POLL_INTERVAL_SECONDS` in a dedicated asyncio task (jitter between polls, exponential backoff on HTTP failures). Serialize all batches through one `asyncio.Lock` before `evaluate_event` / dispatch. Reject config when `gamma_poll_interval_seconds > 0` but `gamma_tag_ids` is empty (fail-fast).
- Consequence: Predictable metadata refresh; no concurrent mutation of runtime counters or rule evaluation; misconfiguration surfaces at startup. `METADATA_REFRESH` remains outside the current rule prefilter until explicitly extended.
