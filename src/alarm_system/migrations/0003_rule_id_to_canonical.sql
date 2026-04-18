BEGIN;

-- One-time mapping from previous demo rule_id values to canonical rule-*-default
-- identities (see deploy/config/rules.sample.json and alert_presets.py).
-- Idempotent: only rows whose payload_json still references the old demo ids are updated.

UPDATE alert_configs
SET
  payload_json = jsonb_set(
    payload_json,
    '{rule_id}',
    '"rule-trader-position-default"'::jsonb,
    true
  ),
  updated_at = NOW()
WHERE payload_json->>'rule_id' = 'rule-user-a-trader-position-politics';

UPDATE alert_configs
SET
  payload_json = jsonb_set(
    payload_json,
    '{rule_id}',
    '"rule-volume-spike-default"'::jsonb,
    true
  ),
  updated_at = NOW()
WHERE payload_json->>'rule_id' = 'rule-user-b-volume-iran';

UPDATE alert_configs
SET
  payload_json = jsonb_set(
    payload_json,
    '{rule_id}',
    '"rule-new-market-liquidity-default"'::jsonb,
    true
  ),
  updated_at = NOW()
WHERE payload_json->>'rule_id' = 'rule-user-c-new-market-liquidity';

COMMIT;
