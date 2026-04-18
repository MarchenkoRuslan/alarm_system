---
name: Prod migration polish ship
overview: Закоммитить миграцию 0003 и документацию, устранить замечания ревью (SQL, терминология, runbook), выкатить в прод с контролируемым порядком API → Worker.
---

# План: правки по ревью + продакшен

## Цель

Внедрить **все** пункты из код-ревью: явные типы в SQL, формулировки без ярлыка «legacy», расширенный runbook (риски 1–5), затем коммит, push и выкат.

---

## A. SQL [`src/alarm_system/migrations/0003_rule_id_to_canonical.sql`](src/alarm_system/migrations/0003_rule_id_to_canonical.sql)

1. **Третий аргумент `jsonb_set`:** для каждого вызова заменить литерал на явное приведение, например `'"rule-trader-position-default"'::jsonb` (или эквивалент `to_jsonb`), чтобы исключить неоднозначность приведения типов на разных версиях PostgreSQL.

2. **Переименование файла (опционально, но рекомендуется для «чистого» репо):** например `0003_rule_id_to_canonical.sql` — обновить везде ссылки в [`docs/architecture/rule-catalog-migration.md`](docs/architecture/rule-catalog-migration.md) и [`docs/architecture/railway-deploy.md`](docs/architecture/railway-deploy.md).

3. **Комментарии в начале файла:** заменить «legacy» на «previous demo rule_id values» / «one-time mapping to canonical ids».

4. **Риски 1 (двойной COMMIT):** в [`rule-catalog-migration.md`](docs/architecture/rule-catalog-migration.md) — короткий подпункт «Совместимость с `apply_sql_migrations`»: скрипт `BEGIN`/`COMMIT` внутри файла + внешний `conn.commit()` после цикла миграций; на практике допустимо; при сомнениях — прогон на стенде.

---

## B. Документация (терминология «legacy»)

Заменить везде на нейтральные формулировки:

- **«Разовая миграция `rule_id`»** / **«маппинг демо-идентификаторов → канонические»** вместо «legacy rule_id migration».
- [`docs/architecture/README.md`](docs/architecture/README.md) — однострочное описание ссылки на `rule-catalog-migration.md` без слова legacy.
- [`docs/architecture/railway-deploy.md`](docs/architecture/railway-deploy.md) — аналогично.
- [`rule-catalog-migration.md`](docs/architecture/rule-catalog-migration.md) — заголовок секции и таблица («Прежний `rule_id` (демо)» / «Канонический `rule_id`»).

---

## C. Runbook: риски 3–5 (расширить `rule-catalog-migration.md`)

1. **Риск 3 — повторный запуск всех `.sql` при каждом старте API:** добавить абзац: текущий [`apply_sql_migrations`](src/alarm_system/api/migrations.py) выполняет все файлы при каждом подъёме; для `0003` после миграции это три дешёвых `UPDATE` с нулём строк; **архитектурный долг** — отсутствие таблицы применённых ревизий (см. ADR) удлинит старт при росте числа миграций; **не регрессия** этого патча.

2. **Риск 4 — порядок деплоя и гонки:** усилить чеклист: если воркер стартует **до** API с тем же Postgres и в БД ещё старые `rule_id`, а в образе только три канонических правила — воркер снова падает на `_build_rule_bindings`. Порядок: **сначала API** (миграции), затем воркер; альтернатива — ручной SQL или временный расширенный `rules.json` (уже описано в п. 3 раздела Rollout).

3. **Риск 5 — только три демо-id:** одна строка в runbook: «если появятся другие неканонические `rule_id`, расширить миграцию или отдельный скрипт».

---

## D. Проверка и выкат

1. `pytest tests/ -q`
2. Один коммит + `git push origin main`
3. Прод: API → Worker, паритет `ALARM_RULES_PATH`, проверки из раздела Verification в `rule-catalog-migration.md`

---

## Порядок работ (todo)

| ID | Задача |
|----|--------|
| sql-jsonb-rename | SQL: `::jsonb`, комментарии, опционально rename файла + ссылки |
| docs-terminology | README, railway-deploy, rule-catalog-migration: без «legacy», секции рисков 3–5 |
| verify-pytest | pytest |
| commit-push | commit + push |
| prod-rollout | деплой по чеклисту |

---

## Итог

План покрывает **все** перечисленные тобой пункты ревью (1–5 + документация), а не только косметику SQL и слово «legacy».
