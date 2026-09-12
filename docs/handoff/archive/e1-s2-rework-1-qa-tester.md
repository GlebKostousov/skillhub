# Выжимка
Роль: qa-tester
Scope: `9353481..ca2fc09`
Вердикт: GATE_OK
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Повторно проверены договор E1-S2, прежние QA/security/perf/reviewer findings и
исправляющий commit `ca2fc09`. Все семь групп исправлений подтверждены:
проверка фактически открытого handle закрывает check/open race и не читает
special file; ошибки конструкторов YAML изолированы; внутренние directory
symlink/junction обходятся в пределах бюджета, внешние цели не перечисляются;
aggregate-границы включительны; duplicate policy детерминирована; McCabe ≤ 3 и
публичный фасад сохранены. Финальный gate: 142 passed, 4 platform skips,
coverage 90,93 %.

# Отчёт
Рецепт: да.

## Contract и scope

Основной договор: `docs/phases/orchestrator-e1.md`, «Слайс E1-S2 — Безопасная
валидация файлового реестра», строки 107–151. Дополнительно сверены
`CONTEXT.md`, `docs/architecture/skillhub.md`,
`docs/architecture/decision-rationale.md`,
`docs/security/threat-model.md`,
`docs/phases/epic-e1-foundation-registry.md` и прежние handoff:
`e1-s2-qa-tester.md`, `e1-s2-security.md`, `e1-s2-perf.md`,
`e1-s2-reviewer.md`.

Проверена только production-дельта `9353481..ca2fc09`. QA изменил только
`tests/test_registry.py` и этот handoff; production и commit не создавались.

## Теория и независимые оракулы

Главный наблюдаемый seam — публичный `SkillRegistry.load()` над реальными
временными деревьями. Результат сравнивается через публичные immutable
`Skill`, `LoadIssue`, `LoadReport`, а пределы берутся из `skillhub.registry`.
Приватный filesystem seam подменяется только в тех местах, где реальную гонку,
внешний handle или special file нельзя воспроизвести одинаково на всех ОС;
итог и отсутствие чтения/перечисления всё равно наблюдаются через публичный
фасад.

Физические Windows junction создаются `mklink /J`, без skip fallback. Отдельные
capability-independent handle-оракулы проверяют containment, запрет внешнего
перечисления и запрет чтения non-regular объекта. Поэтому три Windows-пропуска
обычных symlink из-за WinError 1314 и POSIX-only FIFO skip не являются
единственным доказательством политики.

## Матрица рисков

| Риск | Оракул | Результат |
|---|---|---|
| Success | Публичные immutable модели, точные поля/body/`has_files` | Пройдено |
| Failure isolation | Реальные `ValueError` payload и инъецированный `OverflowError` рядом с валидным sibling | Пройдено |
| Check/open race | Подмена родителя до открытия `SKILL.md`; проверка final path открытого handle до чтения | Пройдено |
| Special file | Реальный POSIX FIFO smoke плюс capability-independent non-regular handle с запрещённым reader | Пройдено |
| Domain traversal | Реальные внутренние junction, nested file и identity-cycle; точные entry/depth edges | Пройдено |
| External traversal | Реальный внешний junction и handle-оракул, падающий при попытке перечисления | Пройдено |
| Aggregate DoS | Exact/overflow для 256 root entries, 128 skill attempts, 1 048 576 bytes, 1024 auxiliary entries и depth 32 | Пройдено |
| Duplicate identity | Первый каталог в стабильном порядке принят, второй получает `duplicate_name` | Пройдено |
| Architecture | Публичные импорты моделей/лимитов и Ruff C90 с McCabe ≤ 3 для всех registry-модулей | Пройдено |
| Disclosure/replay | Внешний marker не попадает в report; повтор и порядок остаются детерминированными | Пройдено |

## Проверка семи групп исправлений

1. Handle/check-open/special file: `_filesystem.py` проверяет final path и
   regular type открытого handle перед bounded read; race и внешний handle
   закрыты тестами на строках 879–966. Новый тест на строках 1292–1333
   доказывает, что non-regular handle не передаётся reader даже без FIFO.
2. YAML isolation: `_document.py` нормализует `OverflowError`,
   `RecursionError`, `ValueError` и `yaml.YAMLError` в `invalid_yaml`.
   Реальные invalid timestamp/huge integer и отдельный injected
   `OverflowError` сохраняют валидный sibling (`tests/test_registry.py`,
   строки 707–756).
3. Bounded internal traversal: реальные Windows junction проходят, цикл
   завершается по identity; exact 1024 entries и depth 32 принимаются, +1
   даёт `directory_limit_exceeded` (строки 969–1033, 1114–1182).
4. External no-enumeration: внешний junction даёт `has_files=False`, а
   capability-independent oracle запрещает вызов iterator для внешнего handle
   (строки 1036–1112).
5. Aggregate limits: root overflow закрывается до parser, skill attempts и
   total bytes ограничивают parser work на точной включительной границе
   (строки 781–877).
6. Duplicate policy: exact collision оставляет первый отсортированный каталог
   и выдаёт стабильный `duplicate_name` второму (строки 758–779).
7. C90/module/public seam: orchestration, parsing, filesystem и platform I/O
   разнесены по приватным модулям; публичные `SkillRegistry`, модели и шесть
   лимитов импортируются из `skillhub.registry`. Ruff с McCabe ≤ 3 проходит.

## Mutant audit

- Удаление `OverflowError` из `_load_yaml` убивается новым sibling-тестом:
  вместо `LoadReport` наружу выходит исключение.
- Удаление проверки `handle.is_regular` убивается новым special-handle тестом:
  запрещённый reader фиксирует чтение.
- Перенос containment только до `open` убивается parent-swap и
  opened-external-handle тестами.
- Перечисление внешнего directory handle убивается guarded iterator.
- Замены включительных сравнений на `>=` убиваются exact-limit тестами root,
  bytes, auxiliary entries и depth.
- Удаление identity set убивается реальным junction-cycle тестом; удаление
  duplicate set — точным collision-тестом.

## Аудит слабых и хрупких тестов

- Три старых file-symlink smoke используют `_try_symlink` и пропускаются на
  Windows без привилегии 1314. Это не blind spot: внутренний/внешний opened
  handle проверяются независимо от capability, а directory junction-сценарии
  реально прошли на Windows.
- FIFO smoke закономерно пропущен на Windows. Новый platform-neutral
  non-regular handle oracle доказывает общий запрет чтения; POSIX smoke
  дополнительно проверяет `O_NONBLOCK` и настоящий FIFO там, где API доступен.
- Race/no-enumeration tests знают приватные имена `filesystem.open_child`,
  `iter_entries` и форму `Handle`. Это осознанная низкоуровневая инъекция;
  они не вызывают приватные validators и утверждают только публичный report и
  факт отсутствия опасной операции. Реальные junction дополняют mock-оракулы.
- `test_windows_final_path_prefixes_are_normalized` структурно связан с
  приватным helper, но покрывает отдельную Win32 boundary и не используется
  как единственное доказательство containment.
- Coverage не считается самостоятельным оракулом: `_posix_io.py` неизбежно
  показывает 0 % в Windows-прогоне, а обязательные свойства закрыты
  platform-neutral тестами и POSIX smoke.
- Happy-only пробелов не найдено: у valid handle, внутренних ссылок, exact
  limits и первого duplicate есть внешняя/non-regular/overflow/+1/second
  отрицательная пара.

## Изменения

- `tests/test_registry.py`: добавлены 2 теста, 69 строк:
  `OverflowError` isolation с валидным sibling и capability-independent запрет
  чтения уже открытого special-file handle.
- `docs/handoff/e1-s2-rework-1-qa-tester.md`: записан этот re-gate.
- Production, зависимости и lock-файл QA не менял; commit не создавался.

## Команды и пропуски

- Baseline `uv run --no-sync pytest -q -rs` → exit 0:
  140 passed, 4 skipped, coverage 90,93 %.
- Фокусный прогон двух новых оракулов и трёх реальных junction-сценариев:
  `uv run --no-sync pytest -q --no-cov -rs <5 node ids>` → exit 0:
  5 passed, 0 skipped.
- Финальный `uv run --no-sync pytest -q -rs` → exit 0:
  142 passed, 4 skipped, branch coverage 90,93 %.
- Skips: 3 старых symlink smoke — WinError 1314; 1 настоящий FIFO smoke —
  POSIX-only. Internal/external/cycle junction tests на Windows не skipped.
- `uv run --no-sync ruff check .` → exit 0.
- `uv run --no-sync ruff format --check .` → exit 0, 85 файлов.
- `uv run --no-sync mypy src tests` → exit 0, 34 source files.
- `uv run --no-sync lint-imports` → exit 0, 1 контракт соблюдён.
- `uv run --no-sync ruff check --select C90 --config
  "lint.mccabe.max-complexity=3" src/skillhub/registry` → exit 0.
- `git diff --check 9353481..ca2fc09` и `git diff --check` → exit 0.
- Ранний запуск только трёх junction node ids без `--no-cov` выполнил все
  3 теста, но вернул exit 1 исключительно из-за глобального coverage
  fail-under на частичном suite; корректный фокусный запуск выше использует
  `--no-cov`.

## Findings

P0: 0.

P1: 0.

P2: 0.

P3: 0.
