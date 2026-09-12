# Выжимка
Роль: qa-tester
Вердикт: GATE_OK
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Дельта `a471630..599bf5f` закрывает `E1-S1-R3-QA-001`: обход top-level
mapping ограничен 16 элементами, включая нестроковые ключи. Узкий allowlist
диагностических скаляров, отказ от обхода неизвестных значений и контейнеров,
безопасность циклов/вложенности и строгий byte cap подтверждены независимыми
оракулами. Все 81 тест и quality gates проходят.

# Отчёт
Рецепт: да

Договор: `docs/phases/orchestrator-e1.md`, раздел `E1-S1`; предыдущий QA:
`docs/handoff/e1-s1-rework-3-qa-tester.md`; проверенный range:
`a471630..599bf5f`.

## Краткая теория

Основной seam — публичный processor `redact_sensitive_fields`, а итоговая
сериализация проверяется через реально настроенный structlog output. Оракулы
не читают production-константу `MAX_FIELDS`: договорный предел 16 задан в
тесте независимо. Пирамида остаётся unit-first; process и HTTP тесты полного
набора сохраняют интеграционные гарантии E1-S1. Coverage использован только как
gate, но не как доказательство поведения.

Mutant «убрать `islice` и обходить весь mapping» убит усиленным тестом:
ожидаемый mutant-run завершился с exit 1 на проверке `visited <= 16`.
Production-реализация на mapping из 100001 пары посетила ровно 16, сохранила
`event`, добавила `[UNSUPPORTED]` и `[TRUNCATED]`, приватное значение не попало
в результат.

## Матрица рисков

| Риск | Независимый оракул | Результат |
|---|---|---|
| Success | Root/health и обе документированные process-точки полного suite | Пройдено |
| Failure | Expected/500, startup/bind и late-response abort полного suite | Пройдено |
| Invalid | Invalid/unknown env и CLI без отражения raw value | Пройдено |
| Domain | AST/import-linter, минимальная граница `core` | Пройдено |
| Replay | Повторные app/health и независимые request ID | Пройдено |
| Attack | 16-entry budget, scalar allowlist, no traversal, unsupported containers, cycles, byte cap | Пройдено |
| Git | Range diff-check, LF index и repo-local настройки | Пройдено |

## Suite audit

- Нестроковые top-level ключи расходуют тот же 16-entry budget: counting
  mapping даёт `visited == 16`, marker unsupported и marker truncation.
- Разрешены только top-level `event`, `error_code`, `status_code`,
  `error_type`, `request_id`, `level`, `timestamp`; неизвестные и поля будущих
  слайсов заменяются на `[REDACTED]`.
- Разрешённые поля принимают только ограниченные строки, `None`, точные
  `bool`, 64-bit integer и finite float. Объекты, огромные integer, NaN,
  mapping, list, tuple и set получают `[UNSUPPORTED]`.
- Counting mapping/list под неизвестными полями и диагностическими полями
  имеют `visited == 0`; приватные вложенные значения не появляются в output.
- Циклический counting list и повторная counting mapping не обходятся,
  заменяются на top-level redaction и не создают `[REFERENCE]`.
- Astral Unicode в нескольких диагностических полях вынуждает bounded
  fallback; полный JSON вместе с переводом строки остаётся не больше 8192
  байт и парсится.
- Остальные тесты сохраняют root/health, HTTP envelopes, startup/process,
  config, accessibility, security headers, Git/EOL/ignore и архитектурные
  границы E1-S1.

## Изменения

- Усилен `tests/test_logging.py`: предел unsupported-key traversal теперь
  проверяется как `<= 16`, а no-traversal закреплён counting-оракулами для
  unknown values, циклов и повторных ссылок; tuple/set добавлены в
  unsupported-container cases.
- Добавлен этот handoff. Production-код, конфигурация и история Git не
  изменялись; коммит не создавался.

## Команды

- `uv sync --frozen` → exit 0, audited 42 packages.
- `uv run --no-sync pytest -q tests/test_logging.py --no-cov` → exit 0,
  10 passed.
- `uv run --no-sync pytest -q` → exit 0, 81 passed, branch coverage 96.53%.
- `uv run --no-sync ruff check .` → exit 0.
- `uv run --no-sync ruff format --check .` → exit 0, 65 файлов.
- `uv run --no-sync mypy src tests` → exit 0, 23 source files.
- `uv run --no-sync lint-imports` → exit 0, 1 контракт соблюдён.
- `git diff --check a471630..599bf5f` и `git diff --check` → exit 0.
- `git ls-files --eol` и `git config --local --list --show-origin` → exit 0;
  индекс tracked text LF, обязательные настройки имеют repo-local источник.
- Mutant-run без `islice` → exit 1 ожидаемо: budget oracle остановил mutant.

## Findings

P0: 0.

P1: 0.

P2: 0.

P3: 0.
