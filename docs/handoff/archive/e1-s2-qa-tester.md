# Выжимка
Роль: qa-tester
Вердикт: GATE_OK
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Проверен range `dd6d135..20748bc` по договору E1-S2. Production-дефекты не
обнаружены. QA усилил только `tests/test_registry.py`: добавлены независимые
оракулы границы 65 536 байт, fallback имени каталога, структуры и типов YAML,
графовых YAML-токенов, безопасного issue path и внутреннего resolve без
зависимости от возможности Windows создавать symlink. Финальный gate:
120 passed, 3 skipped, branch coverage 93,76 %; обязательная symlink-политика
дополнительно доказана capability-independent тестами, поэтому skips не
являются единственным свидетельством.

# Отчёт
Рецепт: да.

Contract path: `docs/phases/orchestrator-e1.md`, раздел «Слайс E1-S2 —
Безопасная валидация файлового реестра». Канон дополнительно сверялся с
`CONTEXT.md`, `docs/architecture/skillhub.md`,
`docs/architecture/decision-rationale.md`,
`docs/security/threat-model.md` и
`docs/phases/epic-e1-foundation-registry.md`.

Проверенный diff: `dd6d135..20748bc`. QA-изменения вне production:
`tests/test_registry.py` и этот handoff. Коммит не создавался.

## Теория и оракулы

Основной seam — публичный `SkillRegistry.load()` над настоящим временным
файловым деревом. Ожидания задаются через публичные immutable-модели,
фиксированные contract literals 64, 1024 и 65 536, точные стабильные причины
и отсутствие недоверенных маркеров в `LoadReport`/structlog.

Для containment используются два независимых filesystem-оракула через
стандартный `Path.resolve`: внутренний resolved target читается, внешний
resolved target отвергается до чтения. Они проходят на любой ОС. Три
физических symlink-smoke теста на Windows пропущены с WinError 1314, но
обязательный allow/deny-контракт ими не исчерпывается.

Пирамида остаётся unit-first: 39 registry-тестов работают через публичный
фасад и реальные файлы, без HTTP/process-слоя. Полный suite сохраняет
интеграционные, process, Git и архитектурные стражи E1-S1. Coverage —
вторичный gate, не поведенческий оракул.

## Матрица рисков

| Риск | Независимый оракул | Результат |
|---|---|---|
| Success | Точное равенство immutable `Skill`/`LoadReport`, body без front matter, nested/empty `has_files` | Пройдено |
| Failure | Битый sibling даёт issue, валидный остаётся; missing/empty root имеют точный результат | Пройдено |
| Invalid | Kebab/fallback и границы 64/1024; пустое body; неверные типы; delimiters; UTF-8; exact/oversize bytes | Пройдено |
| Domain | Любой nested file, включая nested `SKILL.md`, считается; пустые каталоги не считаются | Пройдено |
| Replay | Две загрузки дают равные данные и одинаковый порядок skills/issues | Пройдено |
| Attack | Python YAML tag не исполняется; graph tokens/deep YAML отвергаются; внутренний resolve разрешён, внешний containment закрыт до чтения | Пройдено |
| Disclosure | Issue/log содержит только reason и безопасный относительный ASCII identifier без control/absolute/separator/secret/body/traceback | Пройдено |

## Аудит тестов

- Исходный suite уже хорошо покрывал immutable-модели, metadata name,
  description 1024, body, unsafe Python tag, deep malformed YAML, UTF-8,
  oversize-before-parse, isolation, order, `has_files`, физические symlink и
  безопасный warning.
- Gap: fallback проверялся только на валидном имени каталога; добавлены
  невалидный формат и 65-символьная граница именно для fallback.
- Gap: был проверен только `MAX + 1`; добавлен точный валидный файл на
  65 536 байт, убивающий off-by-one mutant `>=`.
- Gap: внутренний symlink доказывался только тестом, который skip на Windows;
  добавлен deterministic public-seam oracle внутреннего resolve. Внешний
  escape уже имел аналогичный capability-independent deny-оракул.
- Gap: не было отдельных оракулов отсутствующего opening/closing delimiter,
  неверных типов `name`/`caption` с валидным sibling и anchor/alias YAML-графа.
- Gap: warning проверял отсутствие конкретных секретов, но не все свойства
  безопасного идентификатора; теперь проверяются relative, ASCII, no
  separators, no whitespace/control.
- Brittle/private audit: тесты не импортируют приватные loader-функции и не
  повторяют алгоритм парсера. Monkeypatch ограничен стандартным
  `Path.resolve`, все утверждения наблюдаются через публичный фасад.
- Happy-only audit: для каждой основной success-ветки есть invalid/failure
  пара; exact boundary дополнена oversize, внутренний resolve — внешним,
  валидный sibling — malformed/typed sibling.

## Мысленные mutants

- `len(payload) >= MAX_SKILL_FILE_BYTES` вместо `>` падает на exact-limit.
- Пропуск валидации fallback имени падает на `Bad_Fallback` и 65 символах.
- Разрешение любого resolved target падает на внешнем escape; запрет любого
  redirect падает на внутреннем capability-independent oracle.
- Удаление запрета anchor/alias падает на YAML graph case.
- Возврат raw directory name в issue падает на свойствах safe relative path и
  проверках отсутствия marker/absolute path.
- Исключение из typed metadata, прерывающее scan, падает из-за сохранённого
  валидного sibling. Отдельный mutation-runner не запускался.

## Изменения

- `tests/test_registry.py`: добавлено 9 test cases (123 строки) для fallback,
  malformed/typed/graph YAML, exact byte limit, capability-independent
  internal resolve и усиленного safe-path oracle.
- `docs/handoff/e1-s2-qa-tester.md`: записан этот gate.
- Production, README, lock-файл и прочие project docs QA не менял.

## Команды и exit

- Baseline `uv run --no-sync pytest -q` → exit 0: 111 passed, 3 skipped,
  coverage 92,51 %.
- `uv run --no-sync pytest -q tests/test_registry.py --no-cov -rs` → exit 0:
  39 passed, 3 skipped; все три — физическое создание symlink, WinError 1314.
- Финальный `uv run --no-sync pytest -q` → exit 0: 120 passed, 3 skipped,
  branch coverage 93,76 %.
- `uv run --no-sync ruff check .` → exit 0.
- `uv run --no-sync ruff format --check .` → exit 0, 73 файла.
- `uv run --no-sync mypy src tests` → exit 0, 27 source files.
- `uv run --no-sync lint-imports` → exit 0, 1 контракт соблюдён.
- `git diff --check dd6d135..20748bc` и `git diff --check` → exit 0.

## Findings

P0: 0.

P1: 0.

P2: 0.

P3: 0.
