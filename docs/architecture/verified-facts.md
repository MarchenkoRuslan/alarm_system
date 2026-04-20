# Verified Facts: Polymarket Integrations

Date verified: 2026-04-14

This file captures externally validated integration facts used by architecture decisions.

| Area | Fact | Source | Architectural implication |
|---|---|---|---|
| Polymarket CLOB WS overview | Public market websocket channel exists at `wss://ws-subscriptions-clob.polymarket.com/ws/market` and supports `market` subscriptions by `assets_ids` | https://docs.polymarket.com/developers/CLOB/websocket/wss-overview | WS-first ingestion is viable for low-latency event handling |
| Polymarket heartbeats | For market/user channels client must send `PING` every 10s and receive `PONG` | https://docs.polymarket.com/developers/CLOB/websocket/wss-overview | Heartbeat supervisor is mandatory to avoid silent disconnects |
| Market channel events | Market channel emits `book`, `price_change`, `last_trade_price`, plus `new_market` and `market_resolved` when `custom_feature_enabled=true` | https://docs.polymarket.com/developers/CLOB/websocket/market-channel | Canonical event model must support market creation/resolution and trade/orderbook updates; runtime must detect if `new_market` stream is available |
| New market payload | `new_market` event includes `tags`, `condition_id`, `clob_token_ids`, and other metadata | https://docs.polymarket.com/developers/CLOB/websocket/market-channel | For delayed-liquidity alert patterns (e.g. Example C), arming priority is WS `new_market` first; fallback to Gamma discovery sync when WS custom feature stream is unavailable |
| Gamma API auth model | Gamma market/event endpoints are public and do not require authentication | https://docs.polymarket.com/developers/gamma-markets-api/overview | Metadata sync can run without credentials management |
| Gamma tags filtering | `GET /events` and `GET /markets` support tag-based filtering via `tag_id` and related tag controls | https://docs.polymarket.com/developers/gamma-markets-api/get-markets | Topic/category scoping in user-defined presets (including Example B/C) should use tag-based filtering instead of free-text matching |
| Gamma ordering fields | Events endpoint supports ordering by `liquidity`, `volume`, `volume_24hr`, etc. | https://docs.polymarket.com/developers/gamma-markets-api/get-markets | Periodic sync can prioritize high-liquidity/high-volume markets efficiently |
| Telegram Bot webhook | Telegram supports HTTPS webhook updates via `setWebhook`, delivering user messages as `Update` payloads | https://core.telegram.org/bots/api#setwebhook | Interactive bot commands can be processed by dedicated FastAPI webhook endpoint |
| Telegram sendMessage | Bot API `sendMessage` accepts `chat_id` and text payload for command responses and alerts | https://core.telegram.org/bots/api#sendmessage | Delivery provider and webhook command replies can share the same Bot API transport layer |

## Implementation notes (codebase, not external vendor)

| Topic | Behavior in this repo |
|-------|------------------------|
| Gamma periodic poll | Worker (`service_runtime.run`): bootstrap `poll_once` when tags are set; optional background loop starts **only after** bootstrap succeeds, when `ALARM_GAMMA_POLL_INTERVAL_SECONDS` > 0. First periodic sleep runs after bootstrap (no overlap with bootstrap fetch). |
| Gamma `poll_once` concurrency | `GammaMetadataSyncWorker` serializes HTTP `poll_once` with an internal `asyncio.Lock` (one in-flight Gamma fetch per worker). |
| Worker event ordering | Single `asyncio.Lock` in `on_events` so WS and Gamma never evaluate rules concurrently. |
| Config validation | `gamma_poll_interval_seconds > 0` requires non-empty `gamma_tag_ids` (`ServiceRuntimeConfig`). |
| Gauge `ingestion.gamma.last_success_age_sec` | Seconds since the last time a Gamma batch finished processing through `on_events` (rules/delivery for that batch); `-1` means that never succeeded yet. Not the raw HTTP response time. |
| Gamma pipeline errors | JSON log kind `gamma_pipeline_error` with `phase: on_events` when rule/delivery processing fails after a successful HTTP `poll_once` (bootstrap or periodic). |

## Internal compatibility note (2026-04-19)

- `new_market_liquidity` alert presets/filters are type-specific and accept
  only deferred-watch override keys at alert level.
- Legacy numeric filter keys in existing rows are removed by
  `0004_new_market_filters_cleanup.sql` before worker binding.

## Assumptions That Require Follow-up

1. Exact mapping table for product categories (e.g. `Politics`, `Esports`, `Crypto`) to Polymarket tag IDs must be locked in config.
2. Liquidity field semantics for scenario C threshold should be frozen in canonical mapping tests.
3. Final retry/backoff policy for Telegram delivery queue should be load-tested before production.
