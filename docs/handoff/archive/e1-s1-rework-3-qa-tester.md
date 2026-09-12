# Выжимка
Роль: qa-tester
Вердикт: НУЖНА_ДОРАБОТКА
Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Дельта `b7ea37c..66c20b1` закрывает безопасный startup опубликованного
Uvicorn-приложения и поздний HTTP-отказ: invalid/unknown env дают одно
фиксированное событие, а начатый ответ обрывается с безопасным событием и тем
же request ID. Сужение списка чувствительных полей, циклы, глубина, byte cap,
русский текст и прежние root/health/errors/config/Git-инварианты подтверждены.
Gate блокирует неограниченный обход словаря с нестроковыми ключами.

# Отчёт
Рецепт: да

Договор: `docs/phases/orchestrator-e1.md`, раздел `E1-S1`; предыдущий QA:
`docs/handoff/e1-s1-rework-2-qa-tester.md`; проверенный range:
`b7ea37c..66c20b1`.

## Краткая теория

Оракулы поставлены на публичные и наблюдаемые seams: HTTP/ASGI, обе точки
запуска процесса, `Settings`, structlog processor и Git plumbing. Unit и
contract-проверки преобладают; HTTP и отдельный процесс применены только для
интеграционных свойств, которые нельзя доказать подменой. Bounds проверяются
независимо по реально посещённым элементам, памяти и байтам JSON, а не по
coverage. Mutant «не расходовать node budget на неподдерживаемый ключ»
совпадает с текущей веткой и убит новым оракулом: `100000 > 256`.

## Матрица рисков

| Риск | Независимый оракул | Результат |
|---|---|---|
| Success | README launcher, точные HTML `/` и JSON `/health` | Пройдено |
| Failure | Expected/500, bind failure, late-response event и реальный abort | Пройдено |
| Invalid | CLI argv и invalid/unknown `SKILLHUB_*` в обеих process-точках | Пройдено |
| Domain | AST entrypoints и import-linter для `core` | Пройдено |
| Replay | Несколько app/health, независимые request ID | Пройдено |
| Attack | Redaction/cycles/depth/memory/byte cap; unsupported-key traversal | P2 |
| Git | Ignore, attributes, index LF, repo-local config, diff-check | Пройдено |

## Suite audit

- `tests/test_readme_startup.py` проверяет реальный Uvicorn с invalid и unknown
  env: ненулевой exit, одно UTC JSON-событие, отсутствие значения, traceback и
  абсолютного пути.
- `tests/test_web.py` и process-тест проверяют late-response: второй ответ не
  отправляется, передача обрывается, приватное исключение скрыто, событие
  `http.response_aborted` единственно и коррелировано request ID.
- `tests/test_logging.py` фиксирует текущий узкий список чувствительных полей,
  циклы/повторные ссылки, depth/node/memory limits и итоговый byte cap.
- Добавленный counting-map oracle не зависит от времени: словарь из 100000
  нестроковых ключей должен запросить не более 256 пар, но запрашивает все.
- Остальные 79 тестов подтверждают прежние root/health, 400/404/405/500,
  headers/CORS/Host, config, startup, Git/EOL/ignore и русские тексты.

## Изменения

- В `tests/test_logging.py` добавлены oracle узкого E1-S1 field set и
  regression-тест ограниченного обхода нестроковых mapping keys.
- Добавлен этот handoff. Production-код и конфигурация не изменялись, коммит
  не создавался.

## Команды

- `uv sync --frozen` → exit 0, audited 42 packages.
- До нового regression: E1-S1 suite → exit 0, 78 passed, coverage 96.98%.
- Targeted startup/late-response → exit 0, 4 passed.
- Logging без блокирующего regression → exit 0, 8 passed, 1 deselected.
- `uv run --no-sync pytest -q` → exit 1: 1 failed, 79 passed; новый oracle
  получил `visited == 100000` при лимите `<= 256`; coverage 96.98%.
- Ruff check/format, mypy strict и import-linter → exit 0.
- `git diff --check b7ea37c..66c20b1`, `git ls-files --eol` и проверка
  repo-local Git config → exit 0.

## Findings

P0: 0.
P1: 0.

- [P2] [E1-S1-R3-QA-001]
  [`src/skillhub/core/_redaction.py:114-121`] Нестроковый ключ заменяется
  маркером и проходит через `continue`, не уменьшая `nodes_left` и не расходуя
  `text_left`. Поэтому вложенный словарь из `m` таких ключей целиком обходится
  за `O(m)`, хотя публичный bound равен 256; это позволяет недоверенному
  log-value создавать несоразмерную CPU-задержку. Учитывать каждую mapping
  entry как budgeted work и завершать iterator с `[TRUNCATED]` после лимита;
  regression уже добавлен в `tests/test_logging.py`.

P3: 0.
