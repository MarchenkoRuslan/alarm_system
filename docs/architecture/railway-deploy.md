# Railway Deploy Layout (Logical API/Worker split)

Repository strategy: one codebase, two deployable services.

## Services

1. API service
   - Dockerfile: `Dockerfile.api`
   - command: `run-api`
   - public domain: required
   - purpose: `/health`, `/docs`, internal CRUD API, Telegram webhook
2. Worker service
   - Dockerfile: `Dockerfile.worker`
   - command: `run-worker` (backward-compatible alias: `run-service`)
   - public domain: not required
   - purpose: ingestion + rule runtime + delivery dispatch

## Shared dependencies

- Postgres: shared by API and Worker (`ALARM_POSTGRES_DSN`)
- Redis: shared by API and Worker (`ALARM_REDIS_URL`)

## Rule catalog and `ALARM_RULES_PATH`

- The API and Worker **must** use the **same** rule definitions JSON (`ALARM_RULES_PATH`). The image ships [`deploy/config/rules.sample.json`](../../deploy/config/rules.sample.json); bind or copy it to the path your env expects.
- Alert rows in Postgres reference `(rule_id, rule_version)`; those identities must exist in the rules file. See [rule-catalog-migration.md](rule-catalog-migration.md) for demo-to-canonical `rule_id` migration, rollout order, and verification.

## Environment shape

### API required

- `ALARM_ENV=prod`
- `ALARM_POSTGRES_DSN`
- `ALARM_REDIS_URL` (recommended for cache-backed config reads)
- `ALARM_TELEGRAM_BOT_TOKEN`
- `ALARM_TELEGRAM_WEBHOOK_URL` (public HTTPS URL for API webhook endpoint)
- `ALARM_AUTO_APPLY_SQL_MIGRATIONS=true`

### API optional (recommended for security)

- `ALARM_TELEGRAM_WEBHOOK_SECRET` (validated against `X-Telegram-Bot-Api-Secret-Token`)

### Webhook bootstrap behavior

- API startup uses best-effort `setWebhook` (fail-open): webhook registration errors
  are logged, but API process continues to serve `/health`, `/docs`, and `/internal/*`.
- Secret validation remains strict single-secret: when
  `ALARM_TELEGRAM_WEBHOOK_SECRET` is set, mismatched webhook requests are rejected
  with `401`.

### Worker required

- `ALARM_ENV=prod`
- `ALARM_ASSET_IDS`
- `ALARM_REDIS_URL`
- `ALARM_RULES_PATH`
- `ALARM_ALERTS_PATH`
- `ALARM_CHANNEL_BINDINGS_PATH`
- `ALARM_TELEGRAM_BOT_TOKEN` (when sends are enabled)

## Operational order

1. Deploy API and verify `/health` (so SQL migrations including `0003_rule_id_to_canonical.sql` run against Postgres when auto-migrations are enabled).
2. Verify SQL migrations applied (no missing table errors in worker startup).
3. Verify API startup logs for webhook bootstrap:
   - `telegram_webhook_registered` on success;
   - `telegram_webhook_registration_failed` on failure (API still healthy).
4. Validate webhook path with a real Telegram command (`/start`).
5. Deploy Worker and verify `startup_checks` + `startup` logs.

## Post-deploy verification checklist

1. `GET /health` returns `200`.
2. `GET /docs` opens and `GET /internal/alerts?include_disabled=false` returns `200`.
3. `POST /internal/alerts` with JSON `filters_json` succeeds (no `cannot adapt type 'dict'`).
4. Telegram bot `/start` command reaches `/webhooks/telegram` and creates a channel binding.
5. API startup emits one of:
   - `telegram_set_my_commands_registered` (Bot API command menu visible in clients),
   - `telegram_set_my_commands_failed` (fail-open; verify connectivity and restart to retry).
6. Telegram bot `/alerts`, `/status`, and `/help` commands reply in the same chat after `/start`.
7. Telegram bot `/mute 30m` suppresses deliveries (runtime increments
   `delivery_skipped_muted_total` / `skipped_muted`); `/unmute` restores them.
8. If `ALARM_TELEGRAM_WEBHOOK_SECRET` is enabled, webhook requests without valid header are rejected (`401`).
9. If startup logged `telegram_webhook_registration_failed`, re-check Telegram
   connectivity/config and trigger a controlled API restart to retry registration.

## Migration note for existing Railway services

If services were configured before split hardening:

1. API service: switch Dockerfile path to `Dockerfile.api`.
2. Worker service: switch Dockerfile path to `Dockerfile.worker`.
3. Update worker command to `run-worker` (optional; `run-service` remains alias).
4. Redeploy API first, then Worker.
