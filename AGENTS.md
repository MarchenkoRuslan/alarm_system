# AGENTS Guide

Practical guide for AI agents and developers in the `alarm_system` project.

## 1) Project goal

Custom alerting system for prediction markets, scoped to Polymarket only:

- ingestion of Polymarket market signals and, when required, on-chain signals,
- normalization into canonical format,
- signal computation,
- rule evaluation (DSL),
- notification delivery via channel abstraction (MVP provider: Telegram) with explainability.

## 2) Where things are

- Architecture documents: `docs/architecture/`
- Canonical schema: `src/alarm_system/schemas/canonical_event.v1.schema.json`
- Python event models: `src/alarm_system/canonical_event.py`
- Python DSL models: `src/alarm_system/rules_dsl.py`
- Dedup/cooldown helpers: `src/alarm_system/dedup.py`

## 3) Source of truth (read in this order)

1. `docs/architecture/verified-facts.md`  
   Confirmed external API/WS/on-chain constraints.
2. `docs/architecture/adr/ADR-SET-v1.md`  
   Accepted architecture decisions.
3. `docs/architecture/canonical-schema-versioning.md`  
   Contract versioning rules.
4. `docs/architecture/rules-dsl-v1.md`  
   Rule contract, dedup/cooldown, explainability.
5. `docs/architecture/mvp-scope-and-delivery-plan.md`  
   MVP boundaries and delivery plan.

**Also see for production deploy:** `docs/architecture/rule-catalog-migration.md` (rule catalog vs Postgres, migrations, rollout order) and `docs/architecture/railway-deploy.md` (API/worker env and operational order).

## 4) Non-negotiable rules for agents

1. Do not break the canonical schema contract without a versioning procedure.
2. Any integration with an external source must be confirmed in docs and verified links.
3. Rule changes are allowed only via versioning (`rule_version` is immutable).
4. Always preserve explainability (`reason_json`) for each trigger.
5. Duplicate notifications must be blocked by deterministic trigger key.
6. Any fallback/assumption must be documented explicitly.
7. MVP SLA: `source_event_ts -> delivery_enqueue_ts <= 1s` (p95).

## 5) Standard agent workflow

1. Read source-of-truth documents.
2. Determine which layer the task belongs to:
   - ingestion
   - canonical normalization
   - signal compute
   - rules engine
   - delivery
3. Check whether the schema/DSL contract is affected.
4. Apply minimal changes only in the required layer.
5. Update relevant documentation in `docs/architecture/`.
6. Run checks (linter/tests if available).
7. Record risks and impact on MVP scope.

## 6) Definition of done for any change

- The change aligns with the ADR approach.
- Documentation is consistent with the code.
- No regressions in dedup/cooldown/explainability.
- It is clearly described what was done, why, and how to verify.

## 7) Quick scenarios

### Add a new market source

- For the current production scope, only Polymarket is active.
- Extensibility is designed via adapter boundary, but enabling a new market requires ADR + contract tests + SLO revalidation.

### Add a new signal

- Describe the formula and window.
- Add computation in the compute layer.
- Update `rules-dsl-v1.md` (if new operators/semantics are introduced).
- Verify dedup/cooldown behavior.

### Change notification logic

- Preserve core invariants: no-dup, cooldown, suppression.
- Do not remove explainability from payload.
- Do not add channel-specific logic in the hot path: use only `DeliveryPayload` + provider registry.
- Update runbook and acceptance criteria when needed.

### Change interactive Telegram UI

- The bot layer (`src/alarm_system/api/routes/telegram_commands/`,
  `telegram_webhook.py`) is intentionally thin: it produces text +
  inline keyboards and delegates **every** write to the same
  `AlertStore` / `MuteStore` paths as the internal API.
- Do not reimplement rule/DSL logic in callbacks or the wizard.
  Keep business rules in the rules/delivery layers; UI only
  composes `AlertCreateRequest` payloads from product presets.
- Presets live in `src/alarm_system/api/alert_presets.py`; adding a
  new scenario must:
  1. register a `Scenario` there (human label, `alert_type`,
     `rule_id`);
  2. ensure the corresponding rule identity is present in
     `ALARM_RULES_PATH` (or accept the whitelist rejection);
  3. not touch callback dispatch or wizard state machine unless a
     genuinely new step is required.
- `callback_data` is strictly limited to 64 bytes. Long payloads
  go through `SessionStore` with short tokens; the keyboard factory
  must fail loudly (raise) on overflow.
- The `SessionStore` is ephemeral (10 min TTL); never store
  persistent state there.

## 8) What not to do

- Do not treat plan files as implementation source of truth.
- Do not introduce new mandatory technologies without a separate decision.
- Do not add "smart" magic without explicit documentation.
- Do not reintroduce Kalshi/multi-source into MVP without a separate product decision.

## 9) Human-agent handoff

Recommended task format for agents:

- Context: which layer is being changed
- Goal: what should be added
- Constraints: what must not break
- Acceptance criteria: how to know it is done

If data is insufficient, the agent first asks clarifying questions, then proposes a short plan, and only then changes code.
