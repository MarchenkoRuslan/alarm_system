# Rules DSL v1 + Explainability + Dedup/Cooldown

## DSL goals

- Encode complex alert logic with predictable execution.
- Keep runtime deterministic for replay/backfill.
- Produce human-readable trigger reasons.

## Rule structure (v1)

```json
{
  "rule_id": "r_volume_whale_01",
  "tenant_id": "team_alpha",
  "name": "Volume spike with whale participation",
  "severity": "critical",
  "version": 3,
  "expression": {
    "op": "AND",
    "children": [
      {
        "signal": "VolumeSpike",
        "op": "gt",
        "threshold": 2.0,
        "window": { "size_seconds": 300, "slide_seconds": 30 },
        "market_scope": "single_market"
      },
      {
        "signal": "FollowWallet",
        "op": "gte",
        "threshold": 1.0,
        "window": { "size_seconds": 600, "slide_seconds": 60 },
        "market_scope": "event_group"
      }
    ]
  },
  "cooldown_seconds": 180,
  "suppress_if": [
    {
      "signal": "MarketHalt",
      "op": "eq",
      "threshold": 1.0,
      "duration_seconds": 300
    }
  ]
}
```

## Explainability contract

Each trigger must include:

- `rule_id`, `rule_version`, `evaluated_at`
- list of predicate evaluations:
  - signal name
  - operator
  - observed value
  - threshold
  - pass/fail
  - window used
- short summary string suitable for end-user notifications

Example summary:
- `VolumeSpike(2.43>2.00, 5m) AND FollowWallet(1>=1, 10m)`

## Supported operators

- Boolean: `AND`, `OR`, `NOT`
- Comparison: `gt`, `gte`, `lt`, `lte`, `eq`, `ne`
- Aggregate: `delta`, `percentile`, `zscore`

## Dedup strategy

### Trigger key
- Deterministic key: `hash(tenant_id, rule_id, rule_version, scope_id, time_bucket)`.
- `time_bucket = floor(event_ts / bucket_seconds)`.
- `bucket_seconds` is signal-specific (default 60s; configurable per rule).

### Storage
- Store trigger keys in Redis with TTL >= max(cooldown_seconds, bucket_seconds) + safety margin.
- Optional long-term dedup audit in Postgres (`trigger_events.trigger_key` unique).

### Behavior
- If trigger key already exists, skip delivery and record dedup hit metric.
- If key absent, create trigger, persist key, continue to delivery.

## Cooldown strategy

- Cooldown applies per tuple `(tenant_id, rule_id, rule_version, scope_id, channel)`.
- `rule_version` is included so that updating a rule resets the cooldown window.
- During cooldown window, new trigger instances are:
  - suppressed for immediate send,
  - logged as suppressed for audit and analytics.
- Cooldown can be bypassed for severity escalation (`critical` over `warning`) if enabled.

## Execution order

1. Evaluate rule expression.
2. Apply `suppress_if`.
3. Build deterministic trigger key.
4. Dedup check.
5. Cooldown check.
6. Persist trigger + reason.
7. Enqueue notification delivery.
