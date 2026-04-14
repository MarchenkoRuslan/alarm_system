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

## Scenario presets

### Scenario A
- `rule_type = trader_position_update`
- Filters: `category_tags=["Politics"]`, `min_smart_score > 80`, `min_account_age_days > 365`
- Event action one of: open/close/increase/decrease position

### Scenario B
- `rule_type = volume_spike_5m`
- Filters: Iran market tags from Polymarket metadata
- Window: 300 seconds

### Scenario C
- `rule_type = new_market_liquidity`
- Filters: category tags in `{Politics, Esports, Crypto}`
- `deferred_watch.enabled = true`
- `deferred_watch.target_liquidity_usd = 100000`
- Single-fire behavior per `(alert_id, market_id)`

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
- `bucket_seconds` configurable per rule (default 60).

### Storage
- Redis key with TTL `>= max(cooldown_seconds, bucket_seconds) + safety margin`.
- Optional Postgres audit uniqueness on `trigger_events.trigger_key`.

### Behavior
- If key exists: skip enqueue and record `dedup_hit`.
- If key absent: persist trigger and continue.

## Cooldown strategy

- Cooldown key tuple: `(tenant_id, rule_id, rule_version, scope_id, channel)`.
- Channel-aware cooldown is mandatory even if only Telegram provider is enabled initially.
- During cooldown:
  - delivery is suppressed,
  - suppressed instance is still logged.

## Deferred watch semantics (Scenario C)

1. Arm source selection:
   - primary: realtime WS `new_market` -> canonical `market_created`;
   - fallback: Gamma metadata discovery -> synthesized canonical `market_created`.
2. On `market_created` matching filter categories, create durable watch state.
3. On each `liquidity_update`, check threshold crossing.
4. Fire trigger exactly once and mark watch as `fired`.
5. Expire old active watches by TTL if threshold never reached.

## Execution order

1. Event prefilter (`rule_type`, category tags, event type).
2. Evaluate rule expression.
3. Apply `suppress_if`.
4. Build deterministic trigger key.
5. Dedup check.
6. For each channel in `alert.channels`:
   - Cooldown check (keyed per channel).
   - Resolve `ChannelBinding` (destination) for user + channel.
   - Enqueue `DeliveryPayload` for that channel.
7. Persist trigger + reason.

## Delivery layer extension

To add a new channel (e.g. email or webhook):
1. Add value to `DeliveryChannel` enum in `entities.py`.
2. Implement `DeliveryProvider` ABC in a new module (e.g. `providers/email.py`).
3. Register provider in `ProviderRegistry` at app startup.
4. No changes required in rule engine, dedup, or cooldown logic.
