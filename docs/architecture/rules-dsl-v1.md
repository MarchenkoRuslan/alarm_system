# Rules DSL v1 (Polymarket MVP) + Explainability + Dedup/Cooldown

## DSL goals

- Evaluate user alert rules deterministically in realtime.
- Keep latency low via two-phase evaluation (prefilter -> predicates).
- Persist human-readable explainability for every trigger.

## Rule structure (v1)

```json
{
  "rule_id": "r_user_a_positions",
  "tenant_id": "user_a",
  "name": "Top traders in Politics",
  "rule_type": "trader_position_update",
  "severity": "warning",
  "version": 1,
  "filters": {
    "category_tags": ["Politics"],
    "min_smart_score": 80,
    "min_account_age_days": 365
  },
  "expression": {
    "op": "OR",
    "children": [
      { "signal": "PositionOpened", "op": "eq", "threshold": 1, "window": { "size_seconds": 60, "slide_seconds": 10 } },
      { "signal": "PositionClosed", "op": "eq", "threshold": 1, "window": { "size_seconds": 60, "slide_seconds": 10 } },
      { "signal": "PositionIncreased", "op": "eq", "threshold": 1, "window": { "size_seconds": 60, "slide_seconds": 10 } },
      { "signal": "PositionDecreased", "op": "eq", "threshold": 1, "window": { "size_seconds": 60, "slide_seconds": 10 } }
    ]
  },
  "cooldown_seconds": 60,
  "suppress_if": []
}
```

## Identity glossary

- `rule_id` + `version` (rule_version): identity of evaluation semantics.
- `alert_id`: identity of subscription/delivery configuration.
- Required bridge in persistence: each alert references one immutable `(rule_id, rule_version)`.

## Supported `rule_type`

- `trader_position_update`
- `volume_spike_5m`
- `new_market_liquidity`

## Minimal signal inputs (MVP-ready)

To keep alerts informative and fast under load, baseline rule predicates should be built from:

- `price_return_1m_pct` (WS `last_trade_price` / `price_change`)
- `price_return_5m_pct` (WS `last_trade_price` / `price_change`)
- `spread_bps` (WS `book` best bid/ask)
- `book_imbalance_topN` (WS `book` top-N depth)
- `liquidity_usd` (Gamma metadata sync)

These inputs are preferred because they come from already approved external sources and do not require expensive offline modeling.

## Default profile presets (threshold defaults)

Recommended out-of-the-box parameter bundles:

- **Conservative**: `return_1m>=2.0`, `return_5m>=4.0`, `spread_bps<=80`, `abs(imbalance)>=0.30`, `liquidity_usd>=250000`, `cooldown=300`.
- **Balanced**: `return_1m>=1.2`, `return_5m>=2.5`, `spread_bps<=120`, `abs(imbalance)>=0.20`, `liquidity_usd>=100000`, `cooldown=180`.
- **Aggressive**: `return_1m>=0.7`, `return_5m>=1.5`, `spread_bps<=180`, `abs(imbalance)>=0.12`, `liquidity_usd>=50000`, `cooldown=90`.

These are defaults, not hard limits. Users can override each field in custom mode.

## Example presets (illustrative)

### Example A

- `rule_type = trader_position_update`
- Filters: `category_tags=["Politics"]`, `min_smart_score > 80`, `min_account_age_days > 365`
- Event action one of: open/close/increase/decrease position

### Example B

- `rule_type = volume_spike_5m`
- Filters: Iran market tags from Polymarket metadata
- Window: 300 seconds

### Example C

- `rule_type = new_market_liquidity`
- Filters: category tags in `{Politics, Esports, Crypto}`
- `deferred_watch.enabled = true`
- `deferred_watch.target_liquidity_usd = 100000`
- Single-fire behavior per `(alert_id, market_id)`

## General customization surface

Alert authors are not limited to examples. A rule can customize:

- expression composition (`AND`/`OR`/`NOT`) and predicate set;
- signal thresholds and window parameters;
- scope and filters (tags, trader attributes, entity constraints);
- suppression and cooldown behavior;
- channel fanout via alert-level channel routing.

## What user can choose (practical checklist)

At alert creation time, user-configurable knobs are:

- **Template or custom mode**: start from Example A/B/C or build rule from scratch.
- **Rule identity**: alert name and severity (`info`/`warning`/`critical`).
- **Signal logic**:
  - one or more conditions (`signal`, `op`, `threshold`);
  - boolean composition (`AND`/`OR`/`NOT`);
  - window settings (`size_seconds`, `slide_seconds`).
- **Scope/filters**:
  - market tags/categories (`category_tags`);
  - trader quality filters (`min_smart_score`, `min_account_age_days`);
  - market scope (`single_market`, `event_group`, `watchlist`).
- **Noise control**:
  - `cooldown_seconds`;
  - optional suppression rules (`suppress_if`).
- **Delivery**:
  - one or multiple channels (`alert.channels`);
  - destination per channel via `ChannelBinding` (telegram/email/webhook).
- **Delayed-liquidity behavior (optional)**:
  - enable deferred watch;
  - set `target_liquidity_usd` and `ttl_hours`.

## Explainability contract (`reason_json`)

Each trigger must include:

- `rule_id`, `rule_version`, `evaluated_at`
- list of predicate evaluations:
  - signal
  - operator
  - observed value
  - threshold
  - pass/fail
  - window used
- matched filter map (`matched_filters`)
- short summary string for end-user notification message

Example summary:

- `VolumeSpike5m(2.35>2.00) on Iran-tag market`

## Dedup strategy

### Trigger key

- Deterministic key: `hash(tenant_id, rule_id, rule_version, scope_id, time_bucket)`.
- `time_bucket = floor(event_ts / bucket_seconds)`.
- `bucket_seconds` configurable per runtime profile (default 60).

### Storage

- Redis key with TTL `>= bucket_seconds + safety margin`.
- Optional Postgres audit uniqueness on `trigger_events.trigger_key`.

### Behavior

- If key exists: skip enqueue and record `dedup_hit`.
- If key absent: persist trigger and continue.

## Cooldown strategy

- Cooldown key tuple: `(tenant_id, rule_id, rule_version, scope_id, channel)`.
- Channel-aware cooldown is mandatory even if only Telegram provider is enabled initially.
- Cooldown source of truth: `alert.cooldown_seconds`.
- During cooldown:
  - delivery is suppressed,
  - suppressed instance is still logged.

## Deferred watch semantics (for delayed-liquidity alerts, e.g. Example C)

1. Arm source selection:
   - primary: realtime WS `new_market` -> canonical `market_created`;
   - fallback: Gamma metadata discovery -> synthesized canonical `market_created`.
2. On `market_created` matching filter categories, create durable watch state.
3. On each `liquidity_update`, check threshold crossing.
4. Crossing under active suppression does not mark watch as `fired` (watch stays armed).
5. Fire trigger exactly once on first non-suppressed crossing and then mark watch as `fired`.
6. Expire old active watches by TTL if threshold never reached.

## Execution order

1. Event prefilter (`rule_type`, category tags, event type).
   - Prefilter is coarse and may return extra candidates when event tags are missing.
2. Apply strict filter match before predicate evaluation.
   - If rule has `category_tags` and event has no tags/categories, candidate is rejected.
   - If both have tags, at least one intersection is required.
3. Evaluate rule expression.
4. Apply `suppress_if`.
5. Build deterministic trigger key.
6. Dedup check.
7. Mark deferred watch as `fired` (only for non-suppressed accepted crossing).
8. For each channel in `alert.channels`:
   - Cooldown check (keyed per channel).
   - Resolve `ChannelBinding` (destination) for user + channel.
   - Enqueue `DeliveryPayload` for that channel.
9. Persist trigger + reason.

## Runtime coverage snapshot (2026-04-16)

This section documents the current implementation boundary to avoid DSL/runtime drift.

- Implemented in phase-2 runtime path:
  - `category_tags` strict intersection;
  - `iran_tag_only` filter;
  - `min_smart_score` and `min_account_age_days` checks;
  - deferred-watch one-shot delayed crossing behavior;
  - `suppress_if` duration windows via in-memory suppression state keyed by `(alert_id, scope_id, suppress_if index)`.
- Deferred to phase 3:
  - Redis-backed suppression persistence aligned with dedup/cooldown and delivery audit path.

Phase-2 boundary note: `suppress_if` is enforced in runtime, but suppression state is process-local until Phase 3 storage migration.

## Delivery layer extension

To add a new channel (e.g. email or webhook):

1. Add value to `DeliveryChannel` enum in `entities.py`.
2. Implement `DeliveryProvider` ABC in a new module (e.g. `providers/email.py`).
3. Register provider in `ProviderRegistry` at app startup.
4. No changes required in rule engine, dedup, or cooldown logic.
