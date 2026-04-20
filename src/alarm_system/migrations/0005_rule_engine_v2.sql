-- Rule engine v2 schema:
-- - normalized rule graph (sets, rules, groups, predicates)
-- - first-class tags
-- - object/field inverted index for candidate lookup
-- - LISTEN/NOTIFY change channel for runtime hot-reload

CREATE TABLE IF NOT EXISTS rule_sets (
    rule_set_id BIGSERIAL PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE CHECK (version >= 1),
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rule_sets_status
    ON rule_sets (status);

CREATE TABLE IF NOT EXISTS tags (
    tag_id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'polymarket',
    external_tag_id TEXT,
    label TEXT NOT NULL,
    normalized_label TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, normalized_label)
);

CREATE INDEX IF NOT EXISTS idx_tags_external_tag_id
    ON tags (external_tag_id);

CREATE TABLE IF NOT EXISTS rules (
    rule_pk BIGSERIAL PRIMARY KEY,
    rule_set_id BIGINT NOT NULL REFERENCES rule_sets(rule_set_id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    object_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    priority INTEGER NOT NULL DEFAULT 100,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60 CHECK (cooldown_seconds >= 0),
    deferred_watch_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (rule_id, version)
);

CREATE INDEX IF NOT EXISTS idx_rules_rule_set
    ON rules (rule_set_id, enabled);

CREATE INDEX IF NOT EXISTS idx_rules_object_type
    ON rules (object_type, enabled);

CREATE TABLE IF NOT EXISTS rule_groups (
    group_id BIGSERIAL PRIMARY KEY,
    rule_pk BIGINT NOT NULL REFERENCES rules(rule_pk) ON DELETE CASCADE,
    parent_group_id BIGINT REFERENCES rule_groups(group_id) ON DELETE CASCADE,
    bool_op TEXT NOT NULL CHECK (bool_op IN ('AND', 'OR', 'NOT')),
    position INTEGER NOT NULL CHECK (position >= 0),
    UNIQUE (rule_pk, parent_group_id, position)
);

CREATE INDEX IF NOT EXISTS idx_rule_groups_rule_pk
    ON rule_groups (rule_pk);

CREATE TABLE IF NOT EXISTS rule_predicates (
    predicate_id BIGSERIAL PRIMARY KEY,
    group_id BIGINT NOT NULL REFERENCES rule_groups(group_id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    field_path TEXT NOT NULL,
    value_type TEXT NOT NULL,
    comparator TEXT NOT NULL,
    operand_json JSONB NOT NULL,
    window_size_seconds INTEGER NOT NULL DEFAULT 60 CHECK (window_size_seconds > 0),
    window_slide_seconds INTEGER NOT NULL DEFAULT 10 CHECK (window_slide_seconds > 0),
    market_scope TEXT NOT NULL DEFAULT 'single_market',
    UNIQUE (group_id, position)
);

CREATE INDEX IF NOT EXISTS idx_rule_predicates_field_path
    ON rule_predicates (field_path);

CREATE TABLE IF NOT EXISTS rule_tags (
    rule_pk BIGINT NOT NULL REFERENCES rules(rule_pk) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES tags(tag_id) ON DELETE RESTRICT,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (rule_pk, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_rule_tags_tag_id
    ON rule_tags (tag_id);

CREATE TABLE IF NOT EXISTS rule_object_field_index (
    rule_pk BIGINT NOT NULL REFERENCES rules(rule_pk) ON DELETE CASCADE,
    object_type TEXT NOT NULL,
    field_path TEXT NOT NULL,
    tag_id BIGINT REFERENCES tags(tag_id) ON DELETE RESTRICT,
    PRIMARY KEY (rule_pk, object_type, field_path, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_rule_object_field_lookup
    ON rule_object_field_index (object_type, field_path, tag_id);

CREATE OR REPLACE FUNCTION notify_rules_changed() RETURNS trigger AS $$
DECLARE
    payload TEXT;
BEGIN
    payload := json_build_object(
        'table', TG_TABLE_NAME,
        'op', TG_OP,
        'at', NOW()
    )::text;
    PERFORM pg_notify('rules_changed', payload);
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_rules_changed_rule_sets ON rule_sets;
CREATE TRIGGER trg_rules_changed_rule_sets
AFTER INSERT OR UPDATE OR DELETE ON rule_sets
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_rules ON rules;
CREATE TRIGGER trg_rules_changed_rules
AFTER INSERT OR UPDATE OR DELETE ON rules
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_rule_groups ON rule_groups;
CREATE TRIGGER trg_rules_changed_rule_groups
AFTER INSERT OR UPDATE OR DELETE ON rule_groups
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_rule_predicates ON rule_predicates;
CREATE TRIGGER trg_rules_changed_rule_predicates
AFTER INSERT OR UPDATE OR DELETE ON rule_predicates
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_rule_tags ON rule_tags;
CREATE TRIGGER trg_rules_changed_rule_tags
AFTER INSERT OR UPDATE OR DELETE ON rule_tags
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_rule_object_field_index ON rule_object_field_index;
CREATE TRIGGER trg_rules_changed_rule_object_field_index
AFTER INSERT OR UPDATE OR DELETE ON rule_object_field_index
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();

DROP TRIGGER IF EXISTS trg_rules_changed_tags ON tags;
CREATE TRIGGER trg_rules_changed_tags
AFTER INSERT OR UPDATE OR DELETE ON tags
FOR EACH ROW EXECUTE FUNCTION notify_rules_changed();
