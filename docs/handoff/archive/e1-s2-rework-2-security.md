# Выжимка

Рецепт: да.

- Проверены договор E1-S2, AppSec rework-1 и committed delta `3bd870b..6089e28`. Незакоммиченные параллельные изменения рабочего дерева в оценку не включались.
- AppSec-scope: **ДА**. LLM-scope: **НЕТ**: реестр читает `SKILL.md` как недоверенные данные, но не вызывает модель, инструменты или обработчики.
- Вердикт: прежние P1 root TOCTOU и P2 pre-follow закрыты.
- Найдено: **0 × P0, 0 × P1, 0 × P2, 0 × P3**.
- Корень открывается один раз для рабочей сессии, `final_path` именно этого handle сравнивается с ожидаемым resolved path до `iter_entries`; перечисление затем идёт через тот же handle (`src/skillhub/registry/_registry.py:47-53`, `src/skillhub/registry/_filesystem.py:68-82`, `src/skillhub/registry/_filesystem.py:167-193`).
- POSIX применяет `O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC` к root, обычному ребёнку и каждому компоненту разрешённой link-target; symlink сначала читается через `lstat/readlink`, нормализуется и проверяется относительно открытого root (`src/skillhub/registry/_posix_io.py:40-47`, `src/skillhub/registry/_posix_io.py:62-76`, `src/skillhub/registry/_posix_io.py:116-186`).
- Windows сначала открывает reparse point с `OPEN_REPARSE_POINT`, читает и разбирает только junction/symlink tag, отклоняет UNC/device/внешнюю цель и лишь после этого открывает нормализованные компоненты относительно root handle (`src/skillhub/registry/_windows_io.py:287-325`, `src/skillhub/registry/_windows_io.py:328-481`, `src/skillhub/registry/_windows_io.py:533-721`).
- Внутренние ссылки сохраняют требуемую семантику; явной утечки handle или повторного pathname-open после решения о доверии не обнаружено.

**GATE_OK**

# Отчёт

Рецепт: да.

## Фактический поток

1. `SkillRegistry.load()` строго разрешает настроенный root, передаёт ожидаемый путь в `open_registry()` и переводит ожидаемый отказ в безопасный root issue (`src/skillhub/registry/_registry.py:45-53`, `src/skillhub/registry/_registry.py:131-149`).
2. Backend открывает root без следования final reparse/symlink. `open_registry()` проверяет тип и нормализованный `root.final_path` того же handle до передачи `RegistryFilesystem`; только после этого `list_candidates()` перечисляет этот handle (`src/skillhub/registry/_filesystem.py:68-82`, `src/skillhub/registry/_filesystem.py:167-193`).
3. Кандидат и `SKILL.md` открываются относительно уже открытых root/parent handles. Фактический final path и тип проверяются до чтения, а bounded read выполняется с того же token (`src/skillhub/registry/_filesystem.py:125-163`, `src/skillhub/registry/_filesystem.py:195-228`).
4. POSIX plain-компонент открывается с `O_NOFOLLOW`; symlink target сначала получается через `readlink`, лексически сводится к root и затем обходится покомпонентно от root handle. Гонка `stat → open` закрывается `O_NOFOLLOW`, а `stat → readlink` завершается безопасным отказом при смене типа (`src/skillhub/registry/_posix_io.py:62-76`, `src/skillhub/registry/_posix_io.py:116-186`).
5. Windows probe и последующий native open оба используют no-follow. Если объект стал reparse между probe и вторым open, tag читается уже с удерживаемого второго token и снова проходит тот же parser/policy (`src/skillhub/registry/_windows_io.py:340-407`, `src/skillhub/registry/_windows_io.py:445-481`).
6. Разрешённые reparse targets превращаются в относительные компоненты открытого root. UNC, device namespace, иной drive и локальная цель вне root отклоняются до `_open_components()` (`src/skillhub/registry/_windows_io.py:411-442`, `src/skillhub/registry/_windows_io.py:635-707`).
7. Байты строго декодируются как UTF-8, YAML graph/tag запрещён до `safe_load`, затем валидируются metadata и непустое body (`src/skillhub/registry/_document.py:18-35`, `src/skillhub/registry/_document.py:39-83`, `src/skillhub/registry/_document.py:87-126`).
8. После parser и duplicate check descriptor-based DFS вычисляет `has_files` с containment, identity cycle guard, лимитом 1024 entries и глубиной 32 (`src/skillhub/registry/_registry.py:91-119`, `src/skillhub/registry/_filesystem.py:231-304`, `src/skillhub/registry/_limits.py:3-19`).
9. Ожидаемый отказ публикует только стабильные `reason` и bounded safe path identifier; тело, исходное hostile name, абсолютный путь и traceback не добавляются (`src/skillhub/registry/_registry.py:70-89`, `src/skillhub/registry/_registry.py:122-129`, `src/skillhub/registry/_registry.py:150-157`).

## Закрытие прежних findings

### Root TOCTOU — закрыто

- `resolved_root` больше не становится новой базой без проверки открытого объекта: `_validate_root()` сравнивает его с `root.final_path`, полученным из рабочего root token (`src/skillhub/registry/_filesystem.py:167-193`).
- Проверка выполняется до первого `iter_entries`; после неё enumerate/read используют объект `RegistryFilesystem` с тем же root handle (`src/skillhub/registry/_filesystem.py:53-82`).
- Committed attack tests покрывают реальную Windows-подмену root на внешнюю junction и независимую от capability подстановку внешнего открытого root. В обоих случаях запрещены enumeration/read и marker отсутствует (`tests/test_registry.py@6089e28:923-1022`).
- После принятия root его pathname больше не переоткрывается для перечисления: POSIX использует `os.scandir(directory.token)`, Windows — `GetFileInformationByHandleEx(directory.token, ...)` (`src/skillhub/registry/_posix_io.py:79-91`, `src/skillhub/registry/_windows_io.py:245-257`, `src/skillhub/registry/_windows_io.py:751-780`).

### Pre-follow — закрыто

**POSIX**

- `OPEN_FLAGS` содержит `O_NOFOLLOW`, `O_NONBLOCK`, `O_CLOEXEC`; эти flags применяются root и каждому plain-компоненту (`src/skillhub/registry/_posix_io.py:40-47`, `src/skillhub/registry/_posix_io.py:50-59`, `src/skillhub/registry/_posix_io.py:116-118`).
- Link не открывается как target до решения о доверии: `stat(..., follow_symlinks=False)` → `readlink(..., dir_fd=...)` → normalize/`relative_to(root.final_path)` → компонентный open от root (`src/skillhub/registry/_posix_io.py:62-76`, `src/skillhub/registry/_posix_io.py:121-186`).
- Цепочки ограничены 40 hops и 256 компонентами; промежуточные handles закрываются в `finally` (`src/skillhub/registry/_posix_io.py:136-173`, `src/skillhub/registry/_posix_io.py:196-203`).

**Windows**

- И root probe, и root content open используют `FILE_FLAG_OPEN_REPARSE_POINT`; дочерний native open использует `FILE_OPEN_REPARSE_POINT` (`src/skillhub/registry/_windows_io.py:287-325`, `src/skillhub/registry/_windows_io.py:445-481`).
- Поддерживаются только `IO_REPARSE_TAG_MOUNT_POINT` и `IO_REPARSE_TAG_SYMLINK`; неизвестный tag, битый header/offset/UTF-16 и пустая target fail-closed (`src/skillhub/registry/_windows_io.py:533-633`).
- Для junction читается SubstituteName с offset 16; для symlink — SubstituteName с offset 20 и `SYMLINK_FLAG_RELATIVE`. UNC, `\\.\`, `\Device\`, `GLOBALROOT`, device-prefixed relative target, иной drive и external local target отклоняются до normal component open (`src/skillhub/registry/_windows_io.py:553-607`, `src/skillhub/registry/_windows_io.py:635-707`).
- Committed tests фиксируют parser junction/symlink, malformed buffers, no-follow flags и запрет вызова follow-open для UNC/external target (`tests/test_registry.py@6089e28:1408-1601`, `tests/test_registry.py@6089e28:1604-1640`).

## Windows ctypes, offsets и native contract

- `BY_HANDLE_FILE_INFORMATION` соответствует Win32 ABI: 52 bytes; volume serial и high/low file index собираются в identity (`src/skillhub/registry/_windows_io.py:112-128`, `src/skillhub/registry/_windows_io.py:484-507`).
- Неизменённый `FILE_ID_BOTH_DIR_INFO` header на x64 имеет 104 bytes, `ShortName` offset 70, `FileId` offset 96; имя начинается после `sizeof(_DirectoryInfoHeader)` (`src/skillhub/registry/_windows_io.py:130-149`, `src/skillhub/registry/_windows_io.py:769-780`). Эти значения ранее подтверждены runtime-probe rework-1.
- Новые x64 layout статически корректны: `UNICODE_STRING` 16 bytes (`Length` 0, `MaximumLength` 2, `Buffer` 8), `OBJECT_ATTRIBUTES` 48 bytes и `IO_STATUS_BLOCK` 16 bytes. `OBJECT_ATTRIBUTES.Length` задаётся через `ctypes.sizeof`, а `UNICODE_STRING` lengths считаются в UTF-16LE bytes, включая один WCHAR только в `MaximumLength` (`src/skillhub/registry/_windows_io.py:151-181`, `src/skillhub/registry/_windows_io.py:445-481`).
- Для всех используемых Win32/NT функций заданы `argtypes/restype`; `NTSTATUS` остаётся signed 32-bit `c_long`, ошибка переводится через `RtlNtStatusToDosError` (`src/skillhub/registry/_windows_io.py:43-108`, `src/skillhub/registry/_windows_io.py:184-207`, `src/skillhub/registry/_windows_io.py:785-791`).
- Reparse header length считается от 8-byte header; mount/symlink offsets 16/20 соответствуют `REPARSE_DATA_BUFFER`. Name length проверяется на чётность и выход за фактический `ReparseDataLength` до UTF-16 decode (`src/skillhub/registry/_windows_io.py:543-617`).

## Handle/race review

- Root закрывается в `open_registry()` независимо от validation/load outcome; candidate directory удерживается до окончания parser и `has_files`, затем закрывается (`src/skillhub/registry/_filesystem.py:84-106`, `src/skillhub/registry/_filesystem.py:167-185`).
- `SKILL.md` и каждый DFS child закрываются через `finally`; внешний auxiliary target отбрасывается до enumeration (`src/skillhub/registry/_filesystem.py:195-201`, `src/skillhub/registry/_filesystem.py:260-288`).
- POSIX `_make_handle()` закрывает fd при отказе `fstat/final_path`, а каждый промежуточный компонент закрывается после перехода к потомку (`src/skillhub/registry/_posix_io.py:161-173`, `src/skillhub/registry/_posix_io.py:205-219`).
- Windows закрывает root probe, rejected root content token, child probe, raced reparse token и промежуточный handle. Committed fault-injection tests проверяют metadata failures и обе ветки component race (`src/skillhub/registry/_windows_io.py:287-304`, `src/skillhub/registry/_windows_io.py:352-407`, `tests/test_registry.py@6089e28:1689-1824`).
- Очевидного pathname-based check-then-use после trust decision и очевидной ветки утечки handle не найдено.

## Сохранение прежней защиты

- Safe YAML, constructor exception isolation, строгий UTF-8 и metadata bounds не ослаблены: `_document.py` в delta не менялся (`src/skillhub/registry/_document.py:39-126`).
- Лимиты root/skills/total bytes/file/tree/depth продолжают применяться до дорогой следующей стадии (`src/skillhub/registry/_limits.py:3-19`, `src/skillhub/registry/_filesystem.py:68-82`, `src/skillhub/registry/_filesystem.py:153-158`, `src/skillhub/registry/_filesystem.py:210-228`).
- Final-handle containment/type-before-read, short-read loop с одним token, duplicate first-wins, identity cycle guard и safe issue/log contract сохранены (`src/skillhub/registry/_filesystem.py:195-228`, `src/skillhub/registry/_registry.py:91-129`, `src/skillhub/registry/_filesystem.py:279-304`, `src/skillhub/registry/_registry.py:150-157`).
- Internal file/directory links разрешены, external file/directory links не читаются и не перечисляются; committed tests покрывают обе стороны и link cycle (`tests/test_registry.py@6089e28:525-628`, `tests/test_registry.py@6089e28:1073-1225`, `tests/test_registry.py@6089e28:1280-1294`).
- Special-file защита сохранена: тип проверяется до read, POSIX FIFO открывается неблокирующе и не читается (`src/skillhub/registry/_filesystem.py:203-228`, `tests/test_registry.py@6089e28:1896-1956`).

## A–I

### A. Input validation

Строгие UTF-8/front matter/mapping/name/description/caption/body checks и byte bounds применяются до создания `Skill`. YAML aliases, anchors и tags запрещены до construction (`src/skillhub/registry/_document.py:39-126`).

### B. Authz / tenant

N/A: пользователей, tenants и authorization surface в слайсе нет. Filesystem trust boundary проверена в D/G/I.

### C. Secrets / logs

Issue/log содержат только стабильные коды и safe identifier; hostile имя хэшируется, body и абсолютный путь не логируются (`src/skillhub/registry/_registry.py:122-129`, `src/skillhub/registry/_registry.py:150-157`).

### D. Injection / files

Safe YAML и запрет graph/tag предотвращают object construction. Root TOCTOU и link pre-follow закрыты; чтение/перечисление внешней цели до trust decision не найдено в поддерживаемом child flow.

### E. Crypto

N/A: криптографического протокола нет. SHA-256 используется только для необратимого safe identifier hostile имени (`src/skillhub/registry/_registry.py:122-129`).

### F. HTTP

N/A: endpoint, cookie, CORS, CSRF и сетевой клиент в delta отсутствуют. UNC рассматривается только как запрещённая filesystem target.

### G. Replay / integrity

Стабильная сортировка, identity deduplication и duplicate first-wins сохраняют детерминированность. Открытый root связан с ожидаемым путём до enumeration (`src/skillhub/registry/_filesystem.py:68-82`, `src/skillhub/registry/_filesystem.py:145-158`, `src/skillhub/registry/_filesystem.py:187-193`).

### H. DoS / bounds

Ограничены root entries, skill attempts, total/file bytes, auxiliary entries/depth, link hops/components. `O_NONBLOCK` сохраняет отказ без FIFO hang (`src/skillhub/registry/_limits.py:3-19`, `src/skillhub/registry/_posix_io.py:40-47`, `src/skillhub/registry/_posix_io.py:196-203`, `src/skillhub/registry/_windows_io.py:713-721`).

### I. Stored XSS / data exposure

HTML sink в слайсе отсутствует; metadata/body остаются недоверенными строками. Исправления предотвращают возврат внешнего `SKILL.md` как успешного `Skill`; expected-failure surface не раскрывает содержимое или абсолютный путь.

### J. LLM

N/A: LLM-scope **НЕТ**. Prompt/model output/tool execution отсутствуют; файлы только каталогизируются как данные (`docs/phases/orchestrator-e1.md:107-145`).

## Attack-test gaps

Ниже — неблокирующие пробелы доказательной базы; подтверждённой уязвимости за ними не найдено.

1. В committed delta нет ABI assertions для новых `UNICODE_STRING`, `OBJECT_ATTRIBUTES`, `IO_STATUS_BLOCK` и прежнего directory header; текущая оценка новых структур статическая, а runtime layout evidence rework-1 относится к неизменённым `FILE_ID_BOTH_DIR_INFO`/`BY_HANDLE_FILE_INFORMATION`.
2. Реальная root swap attack закреплена для Windows; POSIX имеет общий mismatch-before-enumeration oracle и статическую `O_NOFOLLOW` гарантию, но не отдельный детерминированный native swap между `resolve()` и root open (`tests/test_registry.py@6089e28:923-1022`).
3. Windows forbidden-follow-open test проверяет UNC и external local target, но не перебирает все эквивалентные device spellings (`\\.\`, `\Device\`, `GLOBALROOT`, volume GUID). Реализация отклоняет их одной allow/deny policy до open (`src/skillhub/registry/_windows_io.py:664-707`).
4. Нет безопасного e2e oracle, подтверждающего отсутствие SMB обращения к настоящей UNC target; committed seam запрещает `_open_components()` и тем самым доказывает порядок решения без сетевого side effect (`tests/test_registry.py@6089e28:1441-1488`).
5. Windows internal junction проверяется фактически, а relative symlink — parser/normalization и capability-dependent общим link test; отдельный privileged end-to-end test reparse tag/relative target отсутствует.
6. Новые ветки cleanup покрыты fault injection, но committed delta не повторяет process handle-count probe на длинной серии загрузок. По локальному владению token явной утечки не видно.
7. Подмена промежуточного компонента пути выше настроенного root между `resolve()` и root open отдельно не атакуется. Родительская иерархия настроенного root остаётся доверенной предпосылкой; direct root reparse swap и все ссылки внутри registry boundary закрыты.

## Проверка delta

- `git diff --check 3bd870b..6089e28`: успешно.
- Тесты в рамках этого handoff не запускались; оценены committed implementation и committed attack tests целевого диапазона.
- Незакоммиченные изменения `tests/test_registry.py` и чужие handoff-файлы, появившиеся параллельно, не использованы как доказательство для `6089e28`.
