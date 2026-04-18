# Rule catalog and Postgres alerts (production)

## Invariants

1. **Rule definitions** live in a single JSON file referenced by `ALARM_RULES_PATH`. The API loads allowed `(rule_id, rule_version)` pairs for alert creation; the worker loads full `AlertRuleV1` objects and builds [`_build_rule_bindings`](../../src/alarm_system/service_runtime.py) so every alert in the runtime snapshot references a rule present in that file.
2. **User alerts** in production are stored in Postgres (`alert_configs.payload_json`). The worker does not read `ALARM_ALERTS_PATH` when `ALARM_USE_DATABASE_CONFIG=true`.
3. **API and Worker** must use the **same** `ALARM_RULES_PATH` content (same image build, or identical file on disk/volume). If the API whitelist allows a rule id and the worker file does not contain it, the worker will fail to bind that alert at startup.

## One-time migration: demo `rule_id` to canonical (0003)

Migration [`0003_rule_id_to_canonical.sql`](../../src/alarm_system/migrations/0003_rule_id_to_canonical.sql) updates `payload_json.rule_id` from previous demo identifiers to canonical ids:

| Previous `rule_id` (demo) | Canonical `rule_id` |
|---------------------------|---------------------|
| `rule-user-a-trader-position-politics` | `rule-trader-position-default` |
| `rule-user-b-volume-iran` | `rule-volume-spike-default` |
| `rule-user-c-new-market-liquidity` | `rule-new-market-liquidity-default` |

`rule_version` stays `1`. The migration is idempotent (only rows still matching the previous demo ids are updated).

SQL migrations are applied on **API** startup when `ALARM_AUTO_APPLY_SQL_MIGRATIONS` is true (default), see [`apply_sql_migrations`](../../src/alarm_system/api/migrations.py). The worker does not apply migrations.

### Compatibility with `apply_sql_migrations`

Each migration file is executed as a single script; `0003` wraps updates in `BEGIN`/`COMMIT`. The Python helper also calls `conn.commit()` after running all scripts. In practice this is safe with PostgreSQL + psycopg; if anything looks off on your stack, validate on a staging database first.

### Re-running all `.sql` files on every API start

[`apply_sql_migrations`](../../src/alarm_system/api/migrations.py) runs **every** `*.sql` file in lexical order on **each** API process start. After `0003` has been applied once, the three `UPDATE` statements match zero rows and are cheap. Longer term, the lack of a dedicated “applied revisions” table (see ADR migration notes) means startup cost will grow if many data migrations accumulate; that is existing architectural debt, not introduced by this migration file alone.

### Deploy order and races

If the **worker** starts **before** the API has applied `0003` against shared Postgres, and the database still stores previous demo `rule_id` values while `ALARM_RULES_PATH` points at a **canonical-only** rules file (three `rule-*-default` rules), the worker will fail in [`_build_rule_bindings`](../../src/alarm_system/service_runtime.py) with `Alert references unknown rule identity`. **Deploy the API first** so migrations run, then deploy the worker. If you cannot reorder deploys, run the SQL in `0003` manually against Postgres, or temporarily use a rules JSON that includes both the previous demo rule objects and the canonical ones until the database is updated.

### Other non-canonical `rule_id` values

This migration only rewrites the three demo ids above. If other non-canonical `rule_id` strings appear in production data, extend this migration or add a follow-up script before trimming the rules file.

## Rollout order (Railway / production)

1. **Deploy the API service first** so `0003` runs against Postgres before or alongside workers picking up [`deploy/config/rules.sample.json`](../../deploy/config/rules.sample.json) (three canonical rules only).
2. **Deploy the Worker** with `ALARM_RULES_PATH` pointing at the same catalog as the API (e.g. `/app/deploy/config/rules.json` built from the sample file).
3. If the worker cannot start because Postgres still has previous demo `rule_id` values and the rules file was already trimmed to canonical only, either: run the SQL migration manually against Postgres, or **temporarily** use a rules file that includes both previous demo and canonical rule objects until the migration completes.

## Filter semantics note

Runtime evaluation uses `rule.filters` from the file-backed `AlertRuleV1`, not `alert.filters_json` (see [`rules/runtime.py`](../../src/alarm_system/rules/runtime.py)). Changing `rule_id` from a demo rule definition to a `rule-*-default` rule changes which rule filters apply; confirm that this matches product expectations.

## Verification checklist

- API: `GET /health` returns `200`.
- Worker logs: no `Alert references unknown rule identity` on startup.
- Postgres: `SELECT DISTINCT payload_json->>'rule_id' FROM alert_configs;` shows only `rule-trader-position-default`, `rule-volume-spike-default`, `rule-new-market-liquidity-default` (plus any future custom ids you add to the catalog).
- Telegram: create-alert wizard completes successfully.
