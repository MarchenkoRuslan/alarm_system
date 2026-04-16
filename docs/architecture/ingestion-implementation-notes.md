# Ingestion Implementation Notes

Этот документ фиксирует текущие implementation-level ограничения ingestion-контура Polymarket.

## Scope

- Source: Polymarket only.
- Stream: market WS (`book`, `price_change`, `last_trade_price`, `new_market`, `market_resolved`).
- Fallback: Gamma metadata sync (`tag_id` filtering) вне WS hot path.
- Output contract: `CanonicalEvent` schema version `1.0.0`.

## Runtime responsibilities

- `ws_client.py`: low-level WS transport и `PING`.
- `supervisor.py`: heartbeat watchdog, reconnect/resubscribe loop, batch emission.
- `adapter.py`: `AdapterEnvelope -> CanonicalEvent` нормализация со schema check.
- `mapper.py`: deterministic wire-to-canonical mapping.
- `gamma_sync.py`: metadata refresh events как canonical `metadata_refresh`.

## Determinism and replay safety

- `source_event_id` извлекается из source IDs (`source_event_id`, `event_id`, `id`, `message_id`).
- Canonical `event_id` строится из стабильного tuple:
  `event_type | market_id | source_event_id | payload_hash`.
- Duplicate suppression при reconnect базируется на canonical `event_id` cache в supervisor.
- Duplicate cache ограничен `max_seen_event_ids` (default: 50_000) с FIFO eviction.

## In-memory dedup constraints

- Dedup scope process-local; restart очищает историю.
- Dedup bounded by cardinality (`max_seen_event_ids`), не event-time retention.
- Большие replay окна могут повторно эмитить старые события после eviction.
- Для production-grade delivery path требуется durable/keyed state.

## Reliability policy

- Heartbeat: `PING` every `ping_interval_sec`.
- Health: connection fail если нет `PONG` дольше `pong_timeout_sec`.
- Recovery: reconnect + resubscribe с bounded backoff.
- Acceptance signal: reconnect storm не должен давать duplicate enqueue.

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

| Gate | Что проверяется | Тесты |
| --- | --- | --- |
| Canonical mapping contract | wire payload types -> canonical event types + schema validation | `tests/ingestion/test_polymarket_mapper.py` |
| Deterministic identity | стабильные payload дают стабильные `source_event_id`/`event_id` | `tests/ingestion/test_polymarket_mapper.py` |
| Adapter safety | unsupported payload не падает и учитывается метрикой | `tests/ingestion/test_polymarket_adapter.py` |
| Reconnect + resubscribe | transport errors ведут в reconnect path | `tests/ingestion/test_polymarket_supervisor.py` |
| Heartbeat resilience | timeout по `PONG` ведет к reconnect counters | `tests/ingestion/test_polymarket_supervisor.py` |
| Dedup behavior | duplicate replay suppress + bounded-cache semantics | `tests/ingestion/test_polymarket_reconnect.py`, `tests/ingestion/test_polymarket_supervisor.py` |
| Gamma fallback path | metadata polling эмитит canonical `metadata_refresh` и фильтрует invalid records | `tests/ingestion/test_polymarket_gamma_sync.py` |
