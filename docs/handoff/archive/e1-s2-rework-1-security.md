# Выжимка

Рецепт: да.

- Проверены договор E1-S2, прежний AppSec-отчёт и delta `9353481..ca2fc09`: файловые адаптеры POSIX/Windows, parser, budgets, registry и атакующие тесты.
- AppSec-scope: **ДА**. LLM-scope: **НЕТ**: слайс читает `SKILL.md` как недоверенные данные, но не вызывает модель, инструменты или обработчики.
- Вердикт: AppSec gate **не пройден**.
- Найдено: **0 × P0, 1 × P1, 1 × P2, 0 × P3**.
- P1 подтверждён runtime-probe на Windows: после успешного `Path.resolve()` корень был переименован, а исходный путь заменён junction на внешний каталог. Реестр загрузил внешний `attacker/SKILL.md`, вернул marker из body и не создал issue.
- P2: POSIX использует `O_NONBLOCK`, но не `O_NOFOLLOW`; Windows открывает reparse target без `FILE_FLAG_OPEN_REPARSE_POINT`. Проверка конечного пути происходит только после открытия цели, поэтому внешний symlink/reparse может инициировать блокирующий filesystem/network open, а на Windows — обращение к UNC/SMB до containment-решения.
- Положительно закрыты child-level TOCTOU read, тип фактически открытого объекта, Windows junction containment перед перечислением, internal junction, YAML constructor exceptions, aggregate/tree budgets, duplicate names, обычные безопасные issue/log path и освобождение дескрипторов.

# Отчёт

Рецепт: да.

## Фактический поток данных

1. `SkillRegistry.load()` передаёт сохранённый путь в `_load_registry()`; путь сначала разрешается через `Path.resolve(strict=True)`, затем отдельной операцией открывается через `filesystem.open_path()` (`src/skillhub/registry/_registry.py:45-68`).
2. Открытый root проверяется только на `is_directory`; совпадение `root.final_path` с ранее полученным `resolved_root` не проверяется. После этого с того же root handle перечисляются до 256 имён (`src/skillhub/registry/_registry.py:72-94`, `src/skillhub/registry/_filesystem.py:64-81`).
3. Каждая запись открывается, а её фактические `final_path`, file identity и тип проверяются относительно `root.final_path`; уникальные каталоги расходуют общий лимит attempts (`src/skillhub/registry/_registry.py:111-169`).
4. `SKILL.md` открывается один раз. Containment и `is_regular` проверяются по метаданным открытого handle, после чего с того же token читается не более `min(65 536, remaining)+1` байтов с поддержкой short reads (`src/skillhub/registry/_filesystem.py:84-157`).
5. Байты строго декодируются как UTF-8. Front matter сканируется без alias/anchor/tag и загружается через `yaml.safe_load`; `OverflowError`, `RecursionError`, `ValueError` и `yaml.YAMLError` становятся `invalid_yaml` (`src/skillhub/registry/_document.py:18-35`, `src/skillhub/registry/_document.py:39-83`).
6. После валидации metadata проверяется глобальная уникальность `name`, затем descriptor-based DFS вычисляет `has_files` с final-path containment, file identity, лимитом 1024 entries и глубиной 32 (`src/skillhub/registry/_registry.py:165-196`, `src/skillhub/registry/_filesystem.py:105-225`).
7. Ожидаемый отказ превращается в `LoadIssue` и warning только со стабильными `error_code` и безопасным `skill_path` (`src/skillhub/registry/_registry.py:111-130`, `src/skillhub/registry/_registry.py:199-220`).

## Findings

### P1 — Открытый root не привязан к результату `resolve()`, внешний каталог читается

**Путь атаки.** `_load_registry()` сохраняет `resolved_root`, но `_load_resolved_root()` после отдельного `open_path(resolved_root)` не сравнивает `root.final_path` с `resolved_root` (`src/skillhub/registry/_registry.py:53-68`). `_load_open_root()` принимает любой открытый каталог, а дальнейший containment строится уже от его фактического пути (`src/skillhub/registry/_registry.py:72-82`, `src/skillhub/registry/_registry.py:148-154`). Поэтому подмена root между resolve и open не выявляется: внешний каталог сам становится новой доверенной базой.

**Фактическое доказательство.** На текущем Windows-хосте probe выполнил подмену внутри вызова `filesystem.open_path`: доверенный `skills` после уже завершившегося resolve был переименован, а на его место создан junction к `outside`. Наблюдаемый результат:

- `swapped=True`;
- `loaded=['attacker']`;
- внешний marker присутствует в `repr(LoadReport)`;
- `issues=[]`.

Это прямое нарушение критерия «после resolve выходит за корень — содержимое не читается» (`docs/phases/orchestrator-e1.md:122-137`). Child-level тест подмены родителя не ловит root-level race: он начинает атаку после того, как root уже открыт и принят (`tests/test_registry.py:853-892`).

**Влияние.** Нарушаются containment, целостность каталога и конфиденциальность внешнего валидного `SKILL.md`; body попадает в публичный `LoadReport` и в следующем слайсе может стать доступен через API/UI.

**Исправление.** До любого `root_entry_names()` сравнить нормализованный конечный путь фактически открытого root handle с ранее разрешённым ожидаемым root и fail-closed вернуть безопасный root issue при несовпадении. Проверка должна относиться к тому же handle, который затем перечисляется. Добавить детерминированный тест swap строго между resolve и root open с запрещёнными enumeration/read внешней цели.

### P2 — Внешняя link-target открывается до containment; no-follow гарантия отсутствует

**POSIX.** `OPEN_FLAGS` содержит `O_RDONLY | O_NONBLOCK | O_CLOEXEC`, но не `O_NOFOLLOW`; `open_path()` и `open_child()` следуют symlink до получения `fstat/final_path` (`src/skillhub/registry/_posix_io.py:40-70`, `src/skillhub/registry/_posix_io.py:110-142`). `O_NONBLOCK` закрывает обычный FIFO-hang, но не гарантирует неблокирующий open для FUSE/NFS и других filesystem targets.

**Windows.** `CreateFileW` вызывается с `GENERIC_READ`, `FILE_SHARE_READ|WRITE|DELETE` и только `FILE_FLAG_BACKUP_SEMANTICS`; `FILE_FLAG_OPEN_REPARSE_POINT` отсутствует (`src/skillhub/registry/_windows_io.py:11-20`, `src/skillhub/registry/_windows_io.py:190-202`). `final_path` определяется уже после успешного открытия (`src/skillhub/registry/_windows_io.py:205-238`). Локальный внешний junction затем правильно не перечисляется, но symlink/reparse на UNC может инициировать SMB resolution/authentication или зависнуть до проверки containment.

**Почему текущие тесты недостаточны.** POSIX-тест утверждает только наличие `O_NONBLOCK` и на Windows пропущен; отсутствия follow он не проверяет (`tests/test_registry.py:1266-1283`). Windows-тест использует локальный junction и подтверждает результат `has_files=False`, а capability-independent seam подтверждает запрет enumeration уже открытого внешнего handle (`tests/test_registry.py:1010-1084`). Ни один тест не доказывает отсутствие внешнего open или сетевого side effect.

**Исправление.** Ввести платформенную операцию безопасного открытия links до следования цели: на POSIX — rooted/no-follow или `openat2`-эквивалент с beneath-root policy, `O_NONBLOCK` и `O_CLOEXEC`; на Windows — открыть reparse point без следования, проверить tag/target, запретить UNC/device namespace и только затем разрешить подтверждённую внутреннюю junction. Сохранить поддержку внутренней цели и чтение с одного конечного handle.

## Проверка закрытых поверхностей

### Child-level TOCTOU, final handle path/type/read

- POSIX открывает ребёнка относительно `parent.token`; Windows открывает pathname, но получает `final_path`, identity и тип фактически открытого handle (`src/skillhub/registry/_posix_io.py:59-70`, `src/skillhub/registry/_windows_io.py:135-146`, `src/skillhub/registry/_windows_io.py:205-220`).
- До чтения проверяются `handle.final_path` и `handle.is_regular`, а чтение использует тот же token (`src/skillhub/registry/_filesystem.py:125-157`).
- Детерминированный тест подставляет внешний открытый handle и доказывает `read_attempted=False` (`tests/test_registry.py:894-940`).
- Parent-swap тест подтверждает отсутствие внешнего marker (`tests/test_registry.py:853-892`).
- Эта гарантия не распространяется на root-level race из P1.

### Windows junction и ctypes

- Внутренняя directory junction реально поддерживается и даёт `has_files=True` (`tests/test_registry.py:943-965`).
- Внешняя junction даёт `has_files=False`; отдельный seam проверяет, что открытый внешний directory handle не передаётся в enumeration (`tests/test_registry.py:1010-1084`).
- `BY_HANDLE_FILE_INFORMATION`, `GetFinalPathNameByHandleW`, `GetFileInformationByHandleEx` и `ReadFile` имеют явные `argtypes/restype` (`src/skillhub/registry/_windows_io.py:22-75`, `src/skillhub/registry/_windows_io.py:78-120`).
- Runtime layout-check на x64 Windows дал ожидаемые значения: `FILE_ID_BOTH_DIR_INFO` header 104 bytes, `ShortName` offset 70, `FileId` offset 96; `BY_HANDLE_FILE_INFORMATION` — 52 bytes.
- Share mode `0x7` допускает rename/delete во время сканирования, но открытый file handle остаётся привязан к объекту. Остаточный pathname seam Windows ограничен final-handle containment, кроме принятия root в P1.

### Дескрипторы

- Общие context managers закрывают root, entry, `SKILL.md` и каждый DFS child в `finally` (`src/skillhub/registry/_filesystem.py:38-54`, `src/skillhub/registry/_filesystem.py:125-128`, `src/skillhub/registry/_filesystem.py:180-185`).
- POSIX подавляет ошибку cleanup, Windows вызывает `CloseHandle`; ошибка построения metadata также закрывает уже открытый token (`src/skillhub/registry/_posix_io.py:100-123`, `src/skillhub/registry/_windows_io.py:180-219`).
- Runtime probe после warm-up и 500 загрузок на Windows: process handle count `167 → 167`, delta `0`.

### YAML, budgets и duplicate

- Оба известных constructor payload — invalid timestamp и integer свыше digit limit — изолируются как `invalid_yaml`, сохраняя валидного sibling (`src/skillhub/registry/_document.py:68-83`, `tests/test_registry.py:700-729`).
- Root entries, skill attempts, total bytes, auxiliary entries и depth имеют численные пределы (`src/skillhub/registry/_limits.py:1-19`). Boundary/overflow tests находятся в `tests/test_registry.py:755-850` и `tests/test_registry.py:1088-1139`.
- DFS хранит `(device/volume, file-id)` и завершает внутренний link cycle (`src/skillhub/registry/_filesystem.py:30-35`, `src/skillhub/registry/_filesystem.py:200-225`, `tests/test_registry.py:1141-1157`).
- Первый canonical name в стабильном порядке сохраняется, повтор получает `duplicate_name` до auxiliary walk (`src/skillhub/registry/_registry.py:92-98`, `src/skillhub/registry/_registry.py:173-196`, `tests/test_registry.py:732-752`).

## A. Input validation

- Строгий UTF-8, delimiters, mapping type, kebab-case/64, обязательный description/1024, строковый caption и непустой body применяются до создания `Skill` (`src/skillhub/registry/_document.py:39-64`, `src/skillhub/registry/_document.py:81-126`).
- Размер одного файла и суммарный byte budget применяются до decode/YAML (`src/skillhub/registry/_filesystem.py:139-157`, `src/skillhub/registry/_registry.py:165-175`).
- YAML constructor exceptions закрыты; unsafe graph tokens отвергаются до construction.

## B. Authz / tenant

N/A: слайс не содержит пользователей, tenants или прикладных authorization checks. Filesystem containment является отдельной границей и оценён в D/H/I.

## C. Secrets / logs

- Для ожидаемых отказов в issue/log попадают только стабильная reason и безопасный ASCII identifier; hostile имя хэшируется (`src/skillhub/registry/_registry.py:199-220`).
- `skill_path` разрешён ограничивающим log processor, итоговый JSON остаётся bounded (`src/skillhub/core/_redaction.py:10-29`, `src/skillhub/core/_redaction.py:63-97`).
- Тест проверяет отсутствие body, marker, абсолютного пути и traceback (`tests/test_registry.py:626-667`).
- Логи сами по себе утечку не показали. Однако P1 возвращает внешний body как успешный `Skill`, поэтому общая data-boundary гарантия не выполнена.

## D. Injection / files

- SafeLoader и запрет YAML graph/tag не допускают object construction или Python-tag execution (`src/skillhub/registry/_document.py:3-13`, `src/skillhub/registry/_document.py:68-83`).
- Child-level final-handle containment закрывает прежний check-then-read TOCTOU.
- Не пройдено: root resolve/open race читает внешний каталог — P1.
- Не пройдено: no-follow-before-open отсутствует — P2.

## E. Crypto

N/A: криптографического протокола нет. SHA-256 используется только для необратимого безопасного identifier недоверенного имени пути (`src/skillhub/registry/_registry.py:199-207`).

## F. HTTP

N/A: endpoint, cookie, CORS, CSRF и сетевой клиент в delta отсутствуют. Потенциальный UNC/SMB side effect создаётся файловым reparse resolution и учтён в P2.

## G. Replay / integrity

- Стабильная сортировка root entries и deterministic first-wins duplicate policy дают одинаковый результат для неизменного дерева (`src/skillhub/registry/_filesystem.py:64-81`, `src/skillhub/registry/_registry.py:187-196`).
- Link aliases каталогов дедуплицируются по identity.
- Root race P1 позволяет заменить доверенную базу между двумя системными вызовами и нарушает integrity загрузки.

## H. DoS / bounds

- Численные root/skills/bytes/tree/depth budgets закрывают прежний неограниченный CPU/memory рост; превышение root/aggregate прекращает дальнейший parser work (`src/skillhub/registry/_limits.py:1-19`, `src/skillhub/registry/_registry.py:92-108`, `src/skillhub/registry/_registry.py:125-130`).
- Visited identity закрывает link cycles, а DFS не сортирует auxiliary names.
- Остаточный unbounded open внешней link-target до containment остаётся P2.

## I. Stored XSS / data exposure

- HTML/DOM sink в слайсе отсутствует; metadata/body остаются недоверенными строками для будущего контекстного escaping.
- Expected-failure paths не раскрывают исходное имя или абсолютный путь.
- P1 подтверждённо раскрывает body внешнего валидного `SKILL.md` через успешный `LoadReport`.

## J. LLM

N/A: LLM-scope **НЕТ**. Модель не вызывается, prompt не строится, model output не принимается, инструменты отсутствуют, файлы не исполняются (`docs/phases/orchestrator-e1.md:107-145`).

## Attack-test gaps

1. Root swap после `resolve()` и до `filesystem.open_path()` с утверждениями: внешний root handle не перечислялся, `SKILL.md` не открывался/не читался, marker отсутствует.
2. POSIX actual CI для `O_NONBLOCK`, parent/root swap, symlink escape и descriptor cleanup; на текущем Windows-прогоне FIFO test пропущен.
3. No-follow/beneath-root oracle: внешний symlink/FUSE/NFS target не должен открываться до trust decision.
4. Windows UNC/device reparse attack: отсутствие SMB/network обращения до containment; текущие тесты покрывают только локальный junction.
5. Cleanup fault injection для исключения во время final-path metadata, short read, YAML parse, early `has_files=True` и directory budget overflow; runtime leak не наблюдался, но явного атакующего теста нет.
6. Root final-path normalization cases для drive-letter case, UNC и rename непосредственно между open и первой enumeration.

## Проверочные результаты

- `uv run --no-sync pytest -q --no-cov tests/test_registry.py -rs`: `59 passed, 4 skipped`; три skips — Windows symlink privilege 1314, один — POSIX FIFO.
- Ruff по registry/test и mypy по registry/test: успешно.
- `git diff --check 9353481..ca2fc09`: успешно.
- Windows handle cleanup probe: delta `0` после 500 загрузок.
- Windows root-swap probe: внешний skill и body marker загружены без issue — P1 воспроизводится.

## Рецепт

1. Связать доверенный resolved root с `final_path` того же открытого root handle до enumeration.
2. Разделить безопасную политику открытия root и внутренних links; запретить следование внешней/UNC/device цели до containment, сохранив разрешённые внутренние junction/symlink.
3. Добавить root-race и pre-open side-effect attack tests из gaps.
4. Повторить AppSec gate на Windows и POSIX.
