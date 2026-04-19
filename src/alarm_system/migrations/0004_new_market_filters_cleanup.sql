BEGIN;

-- Remove legacy numeric-bundle keys from new_market_liquidity alert filters.
-- These keys are valid for volume/trader rules but invalid for
-- new_market_liquidity where only deferred-watch overrides are allowed.
-- Idempotent: running multiple times keeps the same payload.

UPDATE alert_configs
SET
  payload_json = jsonb_set(
    payload_json,
    '{filters_json}',
    (
      COALESCE(payload_json->'filters_json', '{}'::jsonb)
      - 'return_1m_pct_min'
      - 'return_5m_pct_min'
      - 'spread_bps_max'
      - 'imbalance_abs_min'
      - 'liquidity_usd_min'
    ),
    true
  ),
  updated_at = NOW()
WHERE payload_json->>'alert_type' = 'new_market_liquidity'
  AND COALESCE(payload_json->'filters_json', '{}'::jsonb) ?| ARRAY[
    'return_1m_pct_min',
    'return_5m_pct_min',
    'spread_bps_max',
    'imbalance_abs_min',
    'liquidity_usd_min'
  ];

COMMIT;
