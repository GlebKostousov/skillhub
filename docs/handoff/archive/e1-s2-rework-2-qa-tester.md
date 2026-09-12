# Выжимка
Роль: qa-tester
Scope: `3bd870b..6089e28`
Вердикт: GATE_OK
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Повторно проверены договор E1-S2, QA/security/logic rework-1 и только указанная
дельта. Root swap отклоняется по конечному пути открытого root handle до
перечисления; внешние link-target проверяются без следования и без сетевого
open; внутренние ссылки сохраняют договорное поведение. `RegistryFilesystem`
скрывает handle lifecycle и бюджеты от orchestration. Прежние гарантии YAML,
лимитов, duplicate policy и неизменяемых моделей сохранены. Финальный gate:
165 passed, 4 platform skips, branch coverage 90,45 % при пороге 85 %.

# Отчёт
Рецепт: да.

## Contract и scope

Основной договор: `docs/phases/orchestrator-e1.md`, раздел «Слайс E1-S2 —
Безопасная валидация файлового реестра», строки 107–151. Сверены
`docs/handoff/e1-s2-rework-1-qa-tester.md`,
`docs/handoff/e1-s2-rework-1-security.md` и
`docs/handoff/e1-s2-rework-1-logic-simple.md`.

Проверена только дельта `3bd870b..6089e28`. QA изменил один тестовый файл и
этот handoff; production, зависимости, lock-файл и commit не создавались.
Порог покрытия принят из owner decision и `pyproject.toml`: 85 %. Текущее
90,45 % не использовалось как основание для дополнительных тестов.

## Теория и независимые оракулы

Основной публичный seam — `SkillRegistry.load()` над реальными временными
деревьями. Ожидания задаются точным равенством публичных frozen-моделей
`Skill`, `LoadIssue` и `LoadReport`, а опасные данные проверяются на отсутствие
в report и warning.

Capability seam применяется только для недетерминируемых гонок и платформенных
операций: подставляется уже открытый handle, запрещающий чтение или
перечисление. На Windows реальные junction отдельно подтверждают root swap,
внутренний/внешний directory link и identity-cycle. Поэтому отсутствие
привилегии на обычный symlink не является единственным доказательством
политики.

Архитектурный seam проверен независимо от поведения: `_registry.py` знает
только семантические операции `RegistryFilesystem` — перечислить кандидатов,
прочитать скилл и определить дополнительные файлы. `Handle`,
`PlatformAdapter`, `_backend`, descriptor lifecycle, link resolution и
внутренние бюджеты остаются в `_filesystem.py` и platform backends.

## Матрица рисков

| Риск | Независимый оракул | Результат |
|---|---|---|
| Root swap | Реальный Windows junction swap и platform-neutral подмена результата `open_path`; внешний handle не перечисляется и не читается | Пройдено |
| Final-handle equality | Подмена открытого root на каталог с другим `final_path` даёт только `root_unreadable` до первого `iter_entries` | Пройдено |
| External pre-follow / network | UNC, device namespace и внешний drive-path отвергаются до `_open_components`; root/child probe используют attribute-only и no-follow | Пройдено |
| Internal links | Реальный directory junction, разрешённый внутренний file handle и nested regular file дают договорный результат | Пройдено |
| External traversal | Внешний directory handle не перечисляется, внешний file handle не читается, marker отсутствует | Пройдено |
| Semantic boundary | Registry orchestration использует `RegistryFilesystem`, а не низкоуровневые handles или backend | Пройдено |
| YAML isolation | Ошибки timestamp, integer limit и инъецированный `OverflowError` дают `invalid_yaml`, валидный sibling остаётся | Пройдено |
| Budgets | Exact/+1 для 256 root entries, 128 attempts, 1 048 576 bytes, 1024 auxiliary entries и depth 32 | Пройдено |
| Duplicate / identity | Стабильный first-wins даёт `duplicate_name`; alias и link-cycle дедуплицируются по identity | Пройдено |
| Immutability / repeat | Frozen value objects, точные поля/body/`has_files`, повтор и порядок неизменны | Пройдено |
| Disclosure / special files | Safe issue/log fields, non-regular handle не читается, внешний marker не раскрывается | Пройдено |

## Mutant audit

In-memory mutant-run, не изменявший production-файлы, убил три критичных
мутации:

- удаление сравнения ожидаемого root с `final_path` открытого handle;
- замена child reparse probe с attribute-only на content access;
- разрешение внешней цели до follow-open.

Для каждой мутации соответствующий oracle завершился ожидаемым assertion
failure; runner вывел `killed` и exit 0.

Статический mutant audit также подтверждает:

- замены включительных budget-сравнений на преждевременные `>=` убиваются
  exact-edge тестами, а удаление overflow-проверок — сценариями `+1`;
- удаление `O_NOFOLLOW`/`FILE_OPEN_REPARSE_POINT` убивается flag-oracle;
- удаление visited identity убивается junction-cycle, удаление duplicate set —
  first-wins collision-тестом;
- удаление YAML exception normalization выпускает исключение вместо
  `LoadReport`;
- перенос containment после read/enumeration убивается запрещающими
  capability seams.

## Аудит слабых и хрупких тестов

- Три старых symlink smoke пропущены на Windows из-за WinError 1314. Их риски
  независимо покрыты реальными junction и открытыми internal/external handle
  seams без capability skip.
- POSIX FIFO закономерно пропущен на Windows. Platform-neutral special-handle
  oracle запрещает чтение, а POSIX-тест дополнительно проверяет `O_NONBLOCK` и
  `O_NOFOLLOW` на POSIX runner. Фактический POSIX backend остаётся задачей
  POSIX CI, а не симуляции Windows.
- Root-race, external-handle и backend-flag тесты знают приватный adapter.
  Это осознанная инъекция на filesystem boundary; доменный результат всё равно
  утверждается через публичный `SkillRegistry.load()`.
- Win32 parser/ABI tests структурно связаны с приватной реализацией, но не
  заменяют публичные junction-сценарии. Они нужны как отдельный pre-follow и
  malformed-buffer fail-closed oracle.
- Новый тест фиксирует только risk-relevant child attribute-only probe.
  No-follow для той же `_open_relative` операции проверяется соседним
  независимым тестом. Coverage-driven тестов не добавлялось.

## Изменения

- `tests/test_registry.py`: добавлен один Windows risk-oracle для
  attribute-only child reparse probe, его cleanup и точного access mode.
- `docs/handoff/e1-s2-rework-2-qa-tester.md`: записан этот re-gate.
- Production и commit QA не менял.

## Команды, skips и порог 85 %

- Baseline `uv run --no-sync pytest -q -rs` → exit 0:
  164 passed, 4 skipped, branch coverage 90,45 %.
- Root/link focus из пяти node ids → exit 0: 5 passed.
- YAML/immutability/budgets focus → exit 0: 10 passed.
- Internal/external links focus → exit 0: 6 passed, 1 capability skip.
- In-memory security mutant-run → exit 0: 3 mutants killed.
- Финальный `uv run --no-sync pytest -q -rs` → exit 0:
  165 passed, 4 skipped, branch coverage 90,45 %; требуемые 85 % достигнуты.
- Skips: 3 обычных symlink smoke — WinError 1314; 1 настоящий FIFO —
  POSIX-only. Windows junction и capability-independent seams не skipped.
- `uv run --no-sync ruff check .` → exit 0.
- `uv run --no-sync ruff format --check .` → exit 0, 89 файлов.
- `uv run --no-sync mypy src tests` → exit 0, 34 source files.
- `uv run --no-sync lint-imports` → exit 0, 1 контракт соблюдён.
- `uv run --no-sync ruff check --select C90 --config
  "lint.mccabe.max-complexity=3" src/skillhub/registry` → exit 0.
- `git diff --check 3bd870b..6089e28` и `git diff --check` → exit 0.

## Findings

P0: 0.

P1: 0.

P2: 0.

P3: 0.
