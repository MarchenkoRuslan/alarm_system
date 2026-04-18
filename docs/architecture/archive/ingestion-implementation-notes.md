# Ingestion Implementation Notes

This document captures the current implementation-level constraints of the Polymarket ingestion domain.

## Scope

- Source: Polymarket only.
- Stream: market WS (`book`, `price_change`, `last_trade_price`, `new_market`, `market_resolved`).
- Fallback: Gamma metadata sync (`tag_id` filtering) outside the WS hot path.
- Output contract: `CanonicalEvent` schema version `1.0.0`.

## Runtime responsibilities

- `ws_client.py`: low-level WS transport and `PING`.
- `supervisor.py`: heartbeat watchdog, reconnect/resubscribe loop, batch emission.
- `adapter.py`: `AdapterEnvelope -> CanonicalEvent` normalization with schema check.
- `mapper.py`: deterministic wire-to-canonical mapping.
- `gamma_sync.py`: metadata refresh events as canonical `metadata_refresh`.

## Determinism and replay safety

- `source_event_id` is extracted from source IDs (`source_event_id`, `event_id`, `id`, `message_id`).
- Canonical `event_id` is built from a stable tuple:
  `event_type | market_id | source_event_id | payload_hash`.
- Duplicate suppression on reconnect is based on canonical `event_id` cache in supervisor.
- Duplicate cache is bounded by `max_seen_event_ids` (default: 50_000) with FIFO eviction.

## In-memory dedup constraints

- Dedup scope is process-local; restart clears history.
- Dedup is bounded by cardinality (`max_seen_event_ids`), not event-time retention.
- Large replay windows may re-emit old events after eviction.
- A production-grade delivery path requires durable/keyed state.

## Reliability policy

- Heartbeat: `PING` every `ping_interval_sec`.
- Health: connection fails if no `PONG` is received for longer than `pong_timeout_sec`.
- Recovery: reconnect + resubscribe with bounded backoff.
- Acceptance signal: reconnect storm must not produce duplicate enqueue.

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
  - `ingestion.gamma.last_market_count`

## Test gates

| Gate | What is verified | Tests |
| --- | --- | --- |
| Canonical mapping contract | wire payload types -> canonical event types + schema validation | `tests/ingestion/test_polymarket_mapper.py` |
| Deterministic identity | stable payloads produce stable `source_event_id`/`event_id` | `tests/ingestion/test_polymarket_mapper.py` |
| Adapter safety | unsupported payload does not crash and is tracked by metrics | `tests/ingestion/test_polymarket_adapter.py` |
| Reconnect + resubscribe | transport errors lead to the reconnect path | `tests/ingestion/test_polymarket_supervisor.py` |
| Heartbeat resilience | `PONG` timeout leads to reconnect counters | `tests/ingestion/test_polymarket_supervisor.py` |
| Dedup behavior | duplicate replay suppress + bounded-cache semantics | `tests/ingestion/test_polymarket_reconnect.py`, `tests/ingestion/test_polymarket_supervisor.py` |
| Gamma fallback path | metadata polling emits canonical `metadata_refresh` and filters invalid records | `tests/ingestion/test_polymarket_gamma_sync.py` |
