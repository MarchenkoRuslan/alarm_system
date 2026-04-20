-- Enforce strict SSOT invariant: exactly one rule_set can be active.
-- Runtime/API may rely on this invariant and fail fast otherwise.

CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_sets_single_active
    ON rule_sets ((status))
    WHERE status = 'active';
