# Выжимка

Роль: qa-tester
Scope: `3cadabe..eab1abf`
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Все блокеры QA/reviewer/logic-simple rework-3 закрыты. Оба поддерживаемых
формата reparse-буфера отклоняют нечётные смещения и длины обоих имён, а
допустимые ненулевые чётные диапазоны возвращают именно `SubstituteName`.
Абсолютная внутренняя UNC-цель под доверенным UNC-root разрешается через
удерживаемый корневой дескриптор; выход на том же общем ресурсе, другой общий
ресурс, переход local → UNC и служебные пространства имён отклоняются до
открытия цели.

Windows resolver имеет один рабочий вход, прежний `_open_components()` удалён.
Русские докстринги изложены естественно. Все прежние проверки E1-S2 проходят.
Финальный результат: 186 passed, 4 ожидаемых platform skip, branch coverage
88,99 % при пороге 85 %. Новые тесты выбраны только по рискам; покрытие не было
целью.

# Отчёт

Рецепт: да.

## Договор, QA rework-3 и delta

Проверен договор E1-S2 из `docs/phases/orchestrator-e1.md`, раздел «Безопасная
валидация файлового реестра», QA rework-3 и committed delta
`3cadabe..eab1abf`. Диапазон содержит один коммит
`eab1abf fix: close fourth E1-S2 rework findings`.

QA изменил только `tests/test_registry.py` и этот handoff. Production-файлы,
зависимости, lock-файл и commit не менялись. Параллельные handoff других ролей
не являются изменениями QA и не входят в проверяемый committed delta.

## Независимые оракулы

- Бинарный oracle самостоятельно собирает `REPARSE_DATA_BUFFER` через
  `struct.pack`, без production-builder и файловой системы. Для mount point и
  symlink проверяются ненулевые чётные диапазоны `SubstituteName` и
  `PrintName`, а также нечётные смещения и длины.
- Чистый path oracle вызывает `target_components()` для двух абсолютных форм
  внутренней UNC-цели и ожидает компоненты относительно доверенного корня.
- Capability oracle подменяет только нативное открытие: допустимая внутренняя
  UNC-цель открывает непосредственный компонент от root handle, а любая
  запрещённая цель обязана завершиться до content-open.
- Структурный oracle подтверждает, что адаптер использует ровно
  `windows_resolver.open_child`, а прежнего `_open_components` в модуле нет.
- Публичный oracle запускает весь набор E1-S1/E1-S2 и проверяет прежние
  контракты загрузки, YAML, лимитов, containment, TOCTOU, ресурсов,
  детерминизма, отчёта и `has_files`.

## Матрица рисков

- P0, чтение внешней цели: local → UNC, внешний путь на том же UNC share,
  другой share и device namespace не доходят до content-open — пройдено.
- P1, структурно неверный reparse-буфер: нечётные offset/length
  `SubstituteName` и `PrintName` для обоих tags завершаются fail-closed —
  пройдено.
- P1, ошибочная семантика доверенного UNC: обе абсолютные внутренние формы
  сводятся к `("shared", ...)` и открываются от удерживаемого root handle —
  пройдено.
- P2, неверный slice при допустимом ненулевом offset: оба tags возвращают
  `SubstituteName`, а не предшествующий `PrintName` — пройдено.
- P2, обход containment: путь вне configured root на том же share и путь на
  другом share отклоняются — пройдено.
- P3, альтернативный state-machine entry: адаптер и resolver имеют один
  рабочий вход, `_open_components` отсутствует — пройдено.
- P3, неестественная русская проза: в изменённых докстрингах используются
  «дескриптор», «адаптер файловых операций» и «цель точки повторного анализа»;
  `Handle`/`handle` остаются только именами Python/Win32 — пройдено.
- Регрессия прежнего E1-S2: полный набор зелёный, состав ожидаемых skips не
  изменился — пройдено.
- Coverage gate: 88,99 % branch coverage при требовании 85 % — пройдено.

## Mutant audit

Mutants применялись только в памяти отдельных Python-процессов; production
файлы не записывались.

- Игнорирование ненулевого `SubstituteNameOffset` убито новым бинарным
  oracle: обе параметризации вернули смесь `PrintName` и цели вместо ожидаемой
  строки.
- Разрешение внешней цели на том же UNC share убито capability oracle: mutant
  дошёл до запрещённого content-open.
- Возврат атрибута `_open_components` убит структурным oracle единственного
  рабочего входа.
- Прежний mutant без проверки выравнивания остаётся убит malformed-buffer
  cases с нечётными смещениями обоих tags; отдельный symlink case теперь также
  фиксирует нечётную длину `SubstituteName`.

Живых mutants в заявленных рисках не осталось.

## Аудит тестов

Добавлены только точечные проверки пробелов доказательной базы:

- допустимые ненулевые чётные offset/length обоих имён для mount point и
  symlink;
- нечётная длина `SubstituteName` в symlink-буфере;
- внешняя UNC-цель на том же share, но выше configured root;
- единый рабочий вход Windows resolver без мёртвого обхода.

Реальный SMB share намеренно не используется: сетевой side effect заменён
детерминированной capability boundary. Три symlink smoke ожидаемо пропущены на
Windows из-за WinError 1314; настоящий FIFO ожидаемо POSIX-only. Соответствующие
риски покрыты нативными Windows/capability и platform-neutral оракулами без
этих skips.

## Команды и результаты

- Baseline `uv run --no-sync pytest -q -rs` → 182 passed, 4 skipped,
  branch coverage 88,99 %.
- Focus parser/UNC/resolver с `--no-cov` → 26 passed, 83 deselected.
- Focus odd/even parser boundaries с `--no-cov` → 14 passed, 95 deselected.
- Три in-memory mutant-run завершились ожидаемыми тестовыми отказами:
  2 failed для offset-mutant, по 1 failed для same-share containment и
  resolver-entry mutants.
- Финальный `uv run --no-sync pytest -q -rs` → 186 passed, 4 skipped,
  branch coverage 88,99 %, coverage gate 85 % достигнут.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно, 109 файлов.
- `uv run --no-sync mypy src tests` → успешно, 40 source files.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- `uv run --no-sync ruff check --select C90 --config
  "lint.mccabe.max-complexity=3" src/skillhub/registry` → успешно.
- `git diff --check 3cadabe..eab1abf` и `git diff --check` → успешно.

## Findings

P0: 0.
P1: 0.
P2: 0.
P3: 0.

**GATE_OK**
