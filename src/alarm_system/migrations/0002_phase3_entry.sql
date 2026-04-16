BEGIN;

CREATE TABLE IF NOT EXISTS channel_bindings (
    binding_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    settings_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    attempt_id TEXT PRIMARY KEY,
    trigger_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    provider_message_id TEXT NULL,
    error_code TEXT NULL,
    error_detail TEXT NULL,
    enqueued_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ NULL,
    next_retry_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS deferred_watches (
    alert_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    target_liquidity_usd DOUBLE PRECISION NOT NULL,
    armed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    fired_at TIMESTAMPTZ NULL,
    PRIMARY KEY (alert_id, market_id)
);

CREATE TABLE IF NOT EXISTS trigger_audit (
    trigger_key TEXT PRIMARY KEY,
    trigger_id TEXT NOT NULL UNIQUE,
    alert_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    reason_json JSONB NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delivery_attempts_trigger_id
    ON delivery_attempts (trigger_id);

CREATE INDEX IF NOT EXISTS idx_trigger_audit_rule
    ON trigger_audit (rule_id, rule_version, created_at);

COMMIT;
