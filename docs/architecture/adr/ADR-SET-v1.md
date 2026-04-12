# ADR Set v1: Custom Alerts Architecture

Status: Accepted  
Date: 2026-04-08

## ADR-0001: Event-driven backbone for core pipeline
- Context: Sources have variable throughput and intermittent instability.
- Decision: Use message-bus topics as boundaries between ingestion, signal computation, rule evaluation, and delivery.
- Alternatives considered: Direct sync pipeline, DB polling loop.
- Consequences: Better fault isolation and horizontal scaling; higher operational complexity.

## ADR-0002: Canonical event schema between adapters and compute
- Context: Polymarket and Kalshi payload shapes differ significantly.
- Decision: Normalize source payloads into schema-versioned canonical events.
- Alternatives considered: Source-specific processing branches.
- Consequences: Cleaner extensibility for future markets; stricter schema governance required.

## ADR-0003: Hybrid compute model (stream + window state)
- Context: Alerts require near-realtime and rolling-window features.
- Decision: Compute streaming metrics with window state persisted in Redis.
- Alternatives considered: Batch-only, stream-only without persisted state.
- Consequences: Low latency + deterministic windows; state TTL and recovery become critical.

## ADR-0004: Separate rule engine service with immutable rule versions
- Context: Alert logic changes frequently and must be auditable.
- Decision: Rule definitions are versioned; each trigger references exact rule version.
- Alternatives considered: Inline rules embedded in workers.
- Consequences: Safe rule evolution and replayability; version migration logic required.

## ADR-0005: At-least-once processing with idempotent triggers
- Context: Websocket reconnects and retries can duplicate events.
- Decision: Accept at-least-once delivery and enforce dedup via deterministic trigger keys.
- Alternatives considered: Exactly-once end-to-end.
- Consequences: Robustness improves; requires careful key design and state retention.

## ADR-0006: Backfill/replay as first-class workflow
- Context: Kalshi separates historical and live data via cutoffs.
- Decision: Maintain checkpointed replay jobs to recompute signals/triggers safely.
- Alternatives considered: Manual ad-hoc backfills.
- Consequences: Reliable onboarding for new rules and post-incident recovery; orchestration overhead increases.

## ADR-0007: On-chain-first whale tracking where available
- Context: Whale signals need verifiable provenance.
- Decision: Use on-chain adapter for Polymarket entity activity whenever data is available.
- Alternatives considered: API-only whale inference.
- Consequences: Better trust and explainability; entity resolution remains probabilistic.

## ADR-0008: Multi-tenant isolation at data and execution layers
- Context: Users/teams can define many custom rules with different sensitivity.
- Decision: Partition rule evaluation and notification quotas by tenant scope.
- Alternatives considered: Global unpartitioned queues.
- Consequences: Reduced blast radius and fair usage; requires tenant-aware scheduling keys.

## ADR-0009: Notification orchestration with suppression and cooldown
- Context: High-frequency markets can trigger noisy alerts.
- Decision: Introduce priority, cooldown windows, and suppression predicates before channel delivery.
- Alternatives considered: Immediate send on every trigger.
- Consequences: Better signal quality and user trust; slightly higher delivery latency.

## ADR-0010: End-to-end observability with trace correlation
- Context: Investigating missed/duplicate alerts requires full chain visibility.
- Decision: Propagate correlation ids from canonical events to delivery attempts.
- Alternatives considered: Metrics-only monitoring.
- Consequences: Faster debugging and SLO governance; telemetry cost increases.
