-- Initial schema for interactive API source-of-truth storage.
-- Postgres is authoritative for alert configs and channel bindings.

CREATE TABLE IF NOT EXISTS alert_configs (
    alert_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_configs_user_id
    ON alert_configs (user_id);

CREATE INDEX IF NOT EXISTS idx_alert_configs_enabled
    ON alert_configs (enabled);

CREATE TABLE IF NOT EXISTS channel_bindings (
    binding_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_user_id
    ON channel_bindings (user_id);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_channel
    ON channel_bindings (channel);

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_binding_target
    ON channel_bindings (user_id, channel, destination);
