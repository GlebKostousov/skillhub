# Выжимка
Роль: qa-tester
Вердикт: GATE_OK
Findings: 0

Дельта `cdf166e..96d417f` закрывает повторный E1-S1 gate: обе server-точки не
раскрывают query, CLI не отражает неверные аргументы, реальные lifecycle/bind
события различимы и имеют UTC-время, а composition/500 boundary остаётся
единственным. Bounds редактирования подтверждены отдельными оракулами на число
шагов, память и байты результата; прежние HTTP/config/log/Git/a11y/RU-doc
инварианты проходят.

# Отчёт
Рецепт: да
Договор: `docs/phases/orchestrator-e1.md`, раздел `E1-S1`.

Проверен только commit range `cdf166e..96d417f` и сохранение ранее принятых
инвариантов. Production-код не изменялся; QA-дельта ограничена тестами и этим
handoff.

## Краткая теория

Оракулы поставлены на наблюдаемых seams: HTTP приложения, документированный
launcher, опубликованный `skillhub.main:app`, машинный process-log, `Settings`
и Git plumbing. Лёгкие unit/contract-проверки преобладают; HTTP и отдельные
процессы используются там, где unit-подмена не доказывает интеграцию. Bounds
проверяются не coverage, а тремя независимыми свойствами: ограниченным числом
извлечённых элементов, ограниченным пиком дополнительной памяти и жёстким
byte-cap валидного JSON. Mutant, схлопывающий все diagnostic codes в
`server_event`, убит точным оракулом различимости.

## Матрица рисков

| Риск | Независимый оракул | Результат |
|---|---|---|
| Success | Точный HTML `/`, JSON `/health`, реальный README launcher | Пройдено |
| Failure | Expected/unexpected HTTP, единственное 500-событие, реальный bind failure | Пройдено |
| Invalid | Неверные/лишние env и CLI argv без raw value, traceback и usage | Пройдено |
| Domain | AST/import-linter для `core`; entrypoints только делегируют | Пройдено |
| Replay | Несколько app/health с независимыми request ID | Пройдено |
| Attack | Query/exception/protocol redaction, hostile Host/Origin, no CORS, localhost bind, bounded traversal/memory/output | Пройдено |
| Git | Ignore, trackable `.env.example`, attributes, index LF, repo-local config | Пройдено |

## Suite audit

- `tests/test_readme_startup.py:261` и `:296` запускают обе server-точки и
  проверяют отсутствие raw query; строки process output дополнительно обязаны
  быть JSON с UTC-временем и ожидаемыми startup-кодами.
- `tests/test_readme_startup.py:331` воспроизводит занятый порт настоящим
  сокетом и проверяет уникальные lifecycle-коды и отдельный `bind_failure`.
- `tests/test_readme_startup.py:386` проверяет failure и attack-варианты CLI:
  неверный port и неизвестный аргумент не отражаются, а результат — одно точное
  машинное событие.
- `tests/test_readme_startup.py:541` требует ровно одно
  `http.unexpected_error` с безопасным типом, кодом и request ID, поэтому
  дублированный источник 500 не пройдёт.
- `tests/test_logging.py:182` считает реально запрошенные элементы широкого
  контейнера и доказывает bounded traversal независимо от wall-clock.
  Существующие tracemalloc и byte-length оракулы отдельно сохраняют пределы
  памяти и выхода.
- HTTP-envelopes 400/404/405, expected/500, headers/CORS, root accessibility и
  replay; fail-closed config; recursive redaction; Git/EOL/ignore; русские
  docstrings и README повторно пройдены.
- Exact assertions фиксируют публичные коды и безопасные поля, но не
  привязываются к приватной реализации; coverage не используется как
  самостоятельный критерий.

## Изменения

- Усилен `tests/test_readme_startup.py`: JSON/UTC oracle для process output,
  реальные startup-коды обеих server-точек, занятый порт, точные CLI events и
  единственность unexpected-error события.
- Усилен `tests/test_logging.py`: добавлен структурный предел числа шагов при
  обходе широкого контейнера.
- Добавлен этот QA-handoff. Production-код и конфигурация не менялись, коммит не
  создавался.

## Команды и exit

- `git diff --stat cdf166e..96d417f` → `0`.
- `git diff --name-status cdf166e..96d417f` → `0`.
- Реальная process-проба launcher + занятого порта → `0`; normal startup дал
  четыре различных UTC lifecycle-кода, child bind failure завершился `3` и
  выдал отдельный безопасный `bind_failure`.
- `uv sync --frozen` → `0`, проверено 42 пакета.
- `uv run --no-sync pytest -q` → `0`, 74 passed, branch coverage 96.41%.
- `uv run --no-sync ruff check .` → `0`.
- `uv run --no-sync ruff format --check .` → `0`, 57 файлов.
- `uv run --no-sync mypy src tests` → `0`, 23 source files.
- `uv run --no-sync lint-imports` → `0`, контракт соблюдён.
- `git diff --check` и `git diff --check cdf166e..96d417f` → `0`.
- `git ls-files --eol` и `git config --local --list --show-origin` → `0`;
  индекс текста LF, обязательные настройки имеют repo-local источник.
- Mutant-run с `_diagnostic_code → server_event` → `1` ожидаемо: точный
  lifecycle oracle упал на первом коде; временный plugin удалён.
