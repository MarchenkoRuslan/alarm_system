# Verified Facts: Polymarket + Kalshi Integrations

Date verified: 2026-04-08

This file captures externally validated integration facts used by architecture decisions.

| Area | Fact | Source | Architectural implication |
|---|---|---|---|
| Polymarket CLOB WS | `market`/`user` websocket channels are documented, including realtime book and trade update types, with client heartbeats | https://docs.polymarket.com/developers/CLOB/websocket/wss-overview | Prefer websocket-first ingestion for low-latency market updates; add heartbeat supervision and reconnect logic |
| Polymarket Market Channel | Market channel emits updates including orderbook, price changes, last trade price | https://docs.polymarket.com/developers/CLOB/websocket/market-channel | Canonical event model must support both snapshot and incremental update event kinds |
| Polymarket Gamma API | Public market/event REST API exists and is unauthenticated | https://docs.polymarket.com/developers/gamma-markets-api/overview | Use Gamma as metadata enrichment layer and market discovery fallback |
| Polymarket On-chain | Core contracts (CTF/Exchange) are published for Polygon deployments | https://docs.polymarket.com/developers/CTF/deployment-resources | Implement on-chain adapter for whale tracking and integrity checks where data is available |
| Kalshi WebSocket | Realtime websocket endpoint and channel subscriptions are documented | https://docs.kalshi.com/websockets/websocket-connection | Treat websocket as primary realtime source for trade/ticker/lifecycle events |
| Kalshi Auth | Authenticated requests use access key headers and signatures | https://docs.kalshi.com/getting_started/quick_start_authenticated_requests | Keep signing isolated in adapter boundary; enforce secret management in deployment |
| Kalshi Historical | Historical data endpoints are separated from live data and gated by cutoffs | https://docs.kalshi.com/getting_started/historical_data | Build first-class backfill/replay path with source checkpointing and cutoff-aware fetch |
| Kalshi Rate Limits | Tiered read/write rate limits are documented | https://docs.kalshi.com/getting_started/rate_limits | Add per-source budgeter, adaptive retry/backoff, and throttling guardrails |

## Assumptions That Require Follow-up

1. Account tier for Kalshi production keys is not yet known.
2. Exact acceptable staleness target per signal family is product-driven and must be finalized.
3. Wallet-to-entity labeling quality target for whale tracking needs product sign-off.
