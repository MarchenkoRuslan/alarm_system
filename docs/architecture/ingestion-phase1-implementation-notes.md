# Ingestion Phase 1 Implementation Notes

This document captures implementation-level assumptions for the first Polymarket ingestion increment.

## Scope frozen for phase 1

- Source: Polymarket only.
- Stream: market WS (`book`, `price_change`, `last_trade_price`, `new_market`, `market_resolved`).
- Fallback: Gamma metadata sync (`tag_id` filtering) outside WS hot path.
- Output contract: `CanonicalEvent` schema version `1.0.0`.

## Runtime responsibilities

- `ws_client.py`: low-level WS transport and `PING` sending.
- `supervisor.py`: heartbeat watchdog, reconnect/resubscribe loop, batch emission.
- `adapter.py`: `AdapterEnvelope -> CanonicalEvent` normalization with schema checks.
- `mapper.py`: deterministic wire-to-canonical mapping strategy.
- `gamma_sync.py`: periodic metadata refresh events as canonical `metadata_refresh`.

## Determinism and replay safety

- `source_event_id` extraction favors source IDs (`source_event_id`, `event_id`, `id`, `message_id`).
- Canonical `event_id` is derived from stable tuple:
  `event_type | market_id | source_event_id | payload_hash`.
- Reconnect duplicate suppression is based on canonical `event_id` cache in supervisor.
- Duplicate cache is bounded by `max_seen_event_ids` (default: 50 000) using a FIFO eviction
  policy: oldest entries are removed first when the limit is reached. Once an `event_id` is
  evicted, the same event will pass dedup again if it re-arrives. This is a deliberate Phase 1
  trade-off (in-memory, no TTL). TTL-based or persistent dedup is deferred to a later phase.

## Reliability policy

- Heartbeat: send `PING` every `ping_interval_sec`.
- Health: fail connection if no `PONG` for `pong_timeout_sec`.
- Recovery: reconnect + resubscribe with bounded backoff.
- Acceptance signal: reconnect storm should not produce duplicate enqueue.

## Observability baseline

- Counters:
  - `ingestion.normalize.success_total`
  - `ingestion.normalize.unsupported_total`
  - `ingestion.supervisor.connected_total`
  - `ingestion.supervisor.reconnect_total`
  - `ingestion.supervisor.errors_total`
  - `ingestion.supervisor.fatal_errors_total`
  - `ingestion.supervisor.heartbeat_timeout_total`
  - `ingestion.supervisor.duplicate_suppressed_total`
- Timings:
  - `ingestion.normalize.latency_ms`
  - `ingestion.gamma.poll_latency_ms`
- Freshness proxy:
  - `ingestion.gamma.last_market_count` gauge

## Known assumptions

- Wire payload keys can vary; mapper uses defensive fallback keys for `market_id` and timestamps.
- Full queueing/persistence semantics are intentionally out of this increment.
- `new_market` availability still depends on Polymarket custom feature rollout.

## Phase 1 test gates

| Gate | What is verified | Test modules |
| --- | --- | --- |
| Canonical mapping contract | WS payload types map to expected canonical event types and pass schema validation | `tests/ingestion/test_polymarket_mapper.py` |
| Deterministic identity | Stable payloads yield stable `source_event_id` and `event_id`; payload mutations change hash/id | `tests/ingestion/test_polymarket_mapper.py` |
| Adapter safety | Unsupported payloads do not crash normalization and are counted via metrics | `tests/ingestion/test_polymarket_adapter.py` |
| Reconnect + resubscribe | Transport errors trigger reconnect path and repeated subscription setup | `tests/ingestion/test_polymarket_supervisor.py` |
| Heartbeat resilience | Missing `PONG` triggers heartbeat timeout and reconnect counters | `tests/ingestion/test_polymarket_supervisor.py` |
| Dedup behavior | Duplicate replay is suppressed; bounded cache allows post-eviction re-emission | `tests/ingestion/test_polymarket_reconnect.py`, `tests/ingestion/test_polymarket_supervisor.py` |
| Gamma fallback path | Metadata polling emits canonical `metadata_refresh` events and filters invalid records | `tests/ingestion/test_polymarket_gamma_sync.py` |
| Observability baseline | Core counters/gauge/timing metrics are asserted in success and failure paths | `tests/ingestion/test_polymarket_adapter.py`, `tests/ingestion/test_polymarket_gamma_sync.py`, `tests/ingestion/test_polymarket_supervisor.py`, `tests/ingestion/test_polymarket_reconnect.py` |
