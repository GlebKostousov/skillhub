# Оперативный план эпика E5 «Стоимость и наблюдаемость»

Дата: 2026-09-12
Ветка: `e5`
База: `d10bed5d294f236b15dcd00af289a4c08331f9fb` (`main` после принятого E3)
Режим: после принятого E4 на `origin/main` (`7ea693c`)
Run: `e5`
Статус: принят as-built на `e5`. Код слайсов S1–S4 закрыт; канонический отчёт — этот файл.

## Понимание эпика

- As-built: тарифы в `config/model-tariffs.yaml`; `UsageLedger` пишет SQLite без текста встречи; `MeteredLlmGateway` резервирует дневной лимит до HTTP; `GET /usage` и `GET /api/usage` показывают `current`, `today` и `entries` в нано-USD.
- Исходная цель: пользователь видит токены и стоимость, историю и дневной итог; превышение лимита останавливается до вызова модели; логи не содержат intent, material и ответ.
- Не входит: уточнения протокола, `finalize`, пакет `protocol` (это E4).

## As-built

- Деньги — целые нано-USD. `calculate_cost` через `Decimal`. Пиковые окна без локали процесса.
- Журнал: `SKILLHUB_USAGE_PATH` (по умолчанию `usage.sqlite3`), схема v1 create-or-reject, индекс `(status, created_at)`.
- `reserve` — `BEGIN IMMEDIATE`. Любая ошибка после reserve вызывает `release`. Просроченный reserved снимается при открытии.
- При заданном `SKILLHUB_DAILY_BUDGET_NANOS` commit режет `cost_nanos`, чтобы дневной held не превысил лимит; фактические токены остаются в строке.
- 429 `daily_budget_exceeded`, текст «Дневной лимит модельных расходов исчерпан».
- `app.state.llm_gateway` — Logging/Metered; inner — `app.state.llm_transport`.
- `usage.complete` пишет `request_id`, `duration_ms`, `status`, `cost_nanos` из allowlist `_DIAGNOSTIC_FIELDS`.
- Маршруты расходов подключаются через `app.routes.extend`, чтобы не ломать frozen E1 (ровно три `include_router`).

## Слайсы

Зависимы: `E5-S1 → E5-S2 → E5-S3 → E5-S4`.

1. `E5-S1` — типизированные модели и тарифы.
2. `E5-S2` — точный ledger и снимок тарифа.
3. `E5-S3` — дневной бюджет и резервирование до HTTP.
4. `E5-S4` — структурные логи и экран расходов.

## Проверка эпика

На frozen SHA `174d22be20f8912a7c2fb172e00c4759e2b11db4` до интеграции E4: `ruff`, `mypy`, `import-linter`, `pytest` — exit 0 (566 passed, покрытие 92.94%).

## Правило движения

Push в `main` только по явной команде владельца. После squash merge next-step — E6 только по явной команде.
