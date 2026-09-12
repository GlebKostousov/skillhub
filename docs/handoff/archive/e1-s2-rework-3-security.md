# Выжимка

Роль: security
Scope: `c60d086..bd997c7`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Проверены договор E1-S2, предыдущий security `GATE_OK`, новые reviewer/perf findings и ровно один committed delta `bd997c7`. Доверенный UNC-root теперь отделён от недоверенной UNC reparse-target: обычный child открывается нативно относительно удерживаемого parent handle, а target из reparse buffer проходит отдельную fail-closed policy до любого follow-open. POSIX и Windows resolver стали итеративными, используют единый cumulative budget и удерживают не более одного owned intermediate handle.

Прежние гарантии root TOCTOU, запрета внешнего/сетевого pre-follow, final-handle containment, safe YAML и безопасного отчёта не ослаблены.

# Отчёт

## Договор и delta

- Договор требует safe YAML, byte bounds, UTF-8, resolved containment, запрет внешних symlink и безопасные относительные warning (`docs/phases/orchestrator-e1.md:107-151`).
- Предыдущий AppSec gate закрыл root TOCTOU и pre-follow и зафиксировал 0 findings (`docs/handoff/e1-s2-rework-2-security.md:1-14`, `docs/handoff/e1-s2-rework-2-security.md:20-76`).
- Новая дельта закрывает два reviewer риска — отказ доверенного UNC-root и смешение Windows ABI/parser/resolver — и perf-риск рекурсивного resolver с локальным, а не cumulative budget (`docs/handoff/e1-s2-rework-2-reviewer.md:17-50`, `docs/handoff/e1-s2-rework-2-perf.md:120-160`).
- В диапазоне изменены только filesystem internals и registry tests; runtime dependencies, HTTP/LLM surface и публичный registry API не менялись.

## Фактический поток

1. `SkillRegistry.load()` делает strict resolve настроенного root и передаёт ожидаемый путь в `open_registry()`; ожидаемый отказ превращается в один безопасный root issue (`src/skillhub/registry/_registry.py:42-53`, `src/skillhub/registry/_registry.py:131-157`).
2. Root открывается no-follow, а тип и `final_path` именно удерживаемого root handle сравниваются с ожидаемым путём до первого перечисления (`src/skillhub/registry/_filesystem.py:167-193`; `src/skillhub/registry/_posix_io.py:41-47`, `src/skillhub/registry/_posix_io.py:61-71`; `src/skillhub/registry/_windows_handles.py:43-57`, `src/skillhub/registry/_windows_handles.py:83-99`).
3. Кандидаты перечисляются через тот же root token; candidate и `SKILL.md` открываются через удерживаемые parent handles, а не через повторно собранный pathname (`src/skillhub/registry/_filesystem.py:68-82`, `src/skillhub/registry/_filesystem.py:99-105`, `src/skillhub/registry/_filesystem.py:139-162`; `src/skillhub/registry/_posix_io.py:73-96`; `src/skillhub/registry/_windows_directory.py:17-47`; `src/skillhub/registry/_windows_resolver.py:31-43`).
4. На Windows probe использует `NtCreateFile` относительно `parent.token` с attribute-only access и `FILE_OPEN_REPARSE_POINT`. Последующий content open снова no-follow; если объект стал reparse между probe и open, target читается с удерживаемого второго token и проходит ту же policy (`src/skillhub/registry/_windows_handles.py:125-176`; `src/skillhub/registry/_windows_resolver.py:81-112`; `tests/test_registry.py@bd997c7:1650-1722`, `tests/test_registry.py@bd997c7:1795-1832`).
5. Обычный child доверенного UNC-root не проходит reparse-target normalizer: оба native открытия выполняются относительно переданного UNC parent handle. Отдельная policy применяется только к строке из reparse buffer (`src/skillhub/registry/_windows_resolver.py:81-112`; `tests/test_registry.py@bd997c7:1520-1570`).
6. Reparse parser принимает только junction/symlink, проверяет header, offsets, длины и UTF-16. UNC, device namespace, иной drive и external local target отклоняются до content/follow-open; внутренняя цель превращается в компоненты относительно удерживаемого root (`src/skillhub/registry/_windows_reparse.py:17-66`, `src/skillhub/registry/_windows_reparse.py:69-151`, `src/skillhub/registry/_windows_reparse.py:154-210`; `src/skillhub/registry/_windows_resolver.py:114-130`; `tests/test_registry.py@bd997c7:1470-1517`, `tests/test_registry.py@bd997c7:1726-1791`).
7. Оба resolver используют цикл и общий `ResolutionBudget`: каждое посещение компонента и каждый link hop расходуют один cumulative предел 256, link hops дополнительно ограничены 40. При restart закрывается owned prefix, root/initial parent не закрываются (`src/skillhub/registry/_resolution.py:6-44`; `src/skillhub/registry/_posix_io.py:50-58`, `src/skillhub/registry/_posix_io.py:129-217`; `src/skillhub/registry/_windows_resolver.py:22-29`, `src/skillhub/registry/_windows_resolver.py:55-169`; `tests/test_registry.py@bd997c7:1573-1646`).
8. Финальный handle проходит containment и type check до bounded read с того же token; descriptor-based DFS ограничен entries/depth и не перечисляет внешний directory handle (`src/skillhub/registry/_filesystem.py:195-228`, `src/skillhub/registry/_filesystem.py:231-304`, `src/skillhub/registry/_filesystem.py:341-350`).
9. Только после этого выполняются strict UTF-8, bounded YAML scan/`safe_load`, metadata/body validation, duplicate policy и `has_files`; issue/log не содержит body, абсолютный путь или traceback (`src/skillhub/registry/_document.py:18-35`, `src/skillhub/registry/_document.py:39-126`; `src/skillhub/registry/_registry.py:70-129`, `src/skillhub/registry/_registry.py:150-157`).

## Проверка требуемых security-инвариантов

### Held-parent NT relative open

`_open_relative()` помещает `parent.token` в `OBJECT_ATTRIBUTES.root_directory`, передаёт только непосредственное имя и всегда включает `FILE_OPEN_REPARSE_POINT` (`src/skillhub/registry/_windows_handles.py:140-176`). После rename/replacement pathname ребёнок читается из старого удерживаемого parent, а не из нового объекта по прежнему имени (`tests/test_registry.py@bd997c7:924-952`). Pathname check-then-use здесь не найден.

### No-follow и reparse-target policy

Root probe/content и child probe/content используют no-follow. Parser и normalizer не выполняют I/O; запрещённая target отклоняется до `_open_relative()` content path. Race `plain probe → reparse content token` повторно обнаруживается по атрибутам уже открытого token (`src/skillhub/registry/_windows_handles.py:83-99`, `src/skillhub/registry/_windows_handles.py:125-137`, `src/skillhub/registry/_windows_resolver.py:81-130`). Внешнего SMB/device follow до решения policy не найдено.

### UNC separation

Доверенный configured UNC-root по-прежнему открывается через extended UNC path, а обычные children — относительно принятого root/parent handle (`src/skillhub/registry/_windows_handles.py:43-57`, `src/skillhub/registry/_windows_handles.py:239-252`; `src/skillhub/registry/_windows_resolver.py:31-43`). Недоверенная абсолютная UNC/device target из reparse data отклоняется отдельно (`src/skillhub/registry/_windows_reparse.py:166-207`). Это закрывает `E1-S2-R2-REV-001`, не возвращая внешний follow-open.

### Iterative resolver, cumulative budget и cleanup

Рекурсивный descent и suffix slicing удалены. Очередь проверяется против остатка единого budget до обработки, component и link вместе не могут превысить 256 steps, Python stack остаётся константным (`src/skillhub/registry/_resolution.py:14-44`; `src/skillhub/registry/_posix_io.py:140-180`; `src/skillhub/registry/_windows_resolver.py:55-94`, `src/skillhub/registry/_windows_resolver.py:114-130`). При каждом переходе прежний owned parent закрывается; `finally` закрывает последний intermediate handle на success и exception (`src/skillhub/registry/_posix_io.py:184-217`; `src/skillhub/registry/_windows_resolver.py:133-169`). Synthetic Windows oracle подтверждает cumulative overflow и равенство opened/closed intermediates (`tests/test_registry.py@bd997c7:1573-1636`).

### Split ABI / parser / I/O

- ABI declarations и Win32/NT signatures изолированы в `_windows_abi.py`; `argtypes/restype`, структуры и константы не размазаны по resolver (`src/skillhub/registry/_windows_abi.py:1-192`).
- Root/relative open, metadata, read, final-path normalization и token cleanup принадлежат `_windows_handles.py` (`src/skillhub/registry/_windows_handles.py:43-260`).
- Handle-based directory enumeration отделено в `_windows_directory.py` (`src/skillhub/registry/_windows_directory.py:1-64`).
- Binary parsing и target normalization — чистый модуль без filesystem I/O (`src/skillhub/registry/_windows_reparse.py:1-210`).
- Resolver владеет только state machine и lifecycle одного intermediate handle, а `_windows_io.py` остался узким приватным adapter facade (`src/skillhub/registry/_windows_resolver.py:1-169`; `src/skillhub/registry/_windows_io.py:1-9`).

Статически проверены Win32 layouts/signatures, reparse offsets 16/20, signed `NTSTATUS`, UTF-16 byte lengths, `OBJECT_ATTRIBUTES.root_directory`, directory header и cleanup error paths (`src/skillhub/registry/_windows_abi.py:97-192`; `src/skillhub/registry/_windows_handles.py:140-207`; `src/skillhub/registry/_windows_reparse.py:69-151`; `tests/test_registry.py@bd997c7:1440-1467`, `tests/test_registry.py@bd997c7:1726-1748`, `tests/test_registry.py@bd997c7:1835-1981`).

### Отсутствие регрессии root TOCTOU / network / containment

Открытый root всё ещё сверяется до enumeration, а enumerate/read используют удерживаемые tokens (`src/skillhub/registry/_filesystem.py:167-193`, `src/skillhub/registry/_filesystem.py:195-228`). Attack oracles по-прежнему запрещают enumeration/read при root swap или final-path mismatch (`tests/test_registry.py@bd997c7:955-1054`). Внешний file handle отклоняется до read, внешний directory handle — до enumeration (`tests/test_registry.py@bd997c7:1057-1102`, `tests/test_registry.py@bd997c7:1204-1257`). Замена `_is_within()` на одну нормализацию плюс `commonpath()` сохраняет fail-closed поведение для разных дисков и устраняет повторную нормализацию родителей (`src/skillhub/registry/_filesystem.py:341-350`).

## A–I

### A. Input validation

Byte limits, strict UTF-8, front matter, safe YAML, запрет aliases/anchors/tags, kebab-case/length checks и непустое body применяются до создания `Skill` (`src/skillhub/registry/_document.py:18-126`; `src/skillhub/registry/_limits.py:1-19`).

### B. Authz / tenant

N/A: users, tenants и authorization surface отсутствуют. Filesystem trust boundary разобрана в D/G.

### C. Secrets / logs

Hostile entry name заменяется bounded SHA-256 identifier; issue/log публикует только safe path и стабильную причину, без payload, absolute path и traceback (`src/skillhub/registry/_registry.py:70-89`, `src/skillhub/registry/_registry.py:122-129`, `src/skillhub/registry/_registry.py:150-157`).

### D. Injection / files

Object construction через YAML закрыта; root и child I/O привязаны к handles. Внешние symlink/junction, UNC/device reparse targets и raced reparse token не следуются до policy/containment. Подтверждённой file injection или external pre-follow ветки нет.

### E. Crypto

N/A: security protocol и cryptographic secret отсутствуют. SHA-256 используется только для необратимого safe identifier (`src/skillhub/registry/_registry.py:122-129`).

### F. HTTP / network

N/A для HTTP: endpoints, cookies, CORS, CSRF и сетевой клиент в delta отсутствуют. Filesystem-induced SMB риск проверен отдельно: недоверенная UNC target отвергается до follow-open, а сеть допустима только для явно настроенного доверенного UNC-root (`src/skillhub/registry/_windows_reparse.py:166-207`; `tests/test_registry.py@bd997c7:1470-1570`).

### G. Replay / integrity

Root identity закреплена открытым handle, final path проверен до enumeration, duplicate policy остаётся first-wins, а directory cycles дедуплицируются по identity (`src/skillhub/registry/_filesystem.py:120-151`, `src/skillhub/registry/_filesystem.py:279-304`; `src/skillhub/registry/_registry.py:91-119`).

### H. DoS / bounds

Сохраняются root/attempt/file/total/tree/depth bounds; resolver добавляет общий максимум 256 component/link steps и 40 link hops при `O(1)` owned handles и без рекурсии (`src/skillhub/registry/_limits.py:1-19`; `src/skillhub/registry/_resolution.py:6-44`). Прикладных retries нет.

### I. Stored XSS / data exposure

HTML sink в слайсе отсутствует, metadata/body остаются недоверенными данными. External payload не читается и не попадает в `Skill`, issue или log (`src/skillhub/registry/_filesystem.py:195-228`; `src/skillhub/registry/_registry.py:150-157`).

### J. LLM

N/A: LLM-scope **НЕТ**. Prompt, model output, tool execution и dynamic code loading отсутствуют; `SKILL.md` только валидируется и каталогизируется как данные (`docs/phases/orchestrator-e1.md:107-145`).

## Attack-test gaps

Ниже только неблокирующие пробелы доказательной базы; подтверждённой уязвимости за ними не найдено.

1. Trusted UNC проверен deterministic seam на двух относительных native opens, но не реальным SMB share; test не доказывает серверные ACL, latency или disconnect semantics (`tests/test_registry.py@bd997c7:1520-1570`).
2. Запрет внешней UNC target доказан отсутствием content `_open_relative()`, но нет безопасного e2e oracle, наблюдающего отсутствие исходящего SMB пакета (`tests/test_registry.py@bd997c7:1470-1517`).
3. После split нет явных `ctypes.sizeof`/field-offset assertions для всех ABI-структур; layouts проверены статически, а native calls покрывают только исполняемые ветки (`src/skillhub/registry/_windows_abi.py:97-192`).
4. Cumulative cleanup oracle Windows синтетический; нет process handle-count soak и отдельного POSIX fault-injection oracle на длинной link chain (`tests/test_registry.py@bd997c7:1573-1646`).
5. Device spellings не перебраны исчерпывающе, хотя единая policy отклоняет UNC, `\\.\`, `\Device\`, `GLOBALROOT` и namespace-prefixed формы до follow-open (`src/skillhub/registry/_windows_reparse.py:166-207`).
6. Реальный privileged end-to-end relative Windows symlink не закреплён отдельно; parser/relative policy, no-follow options и junction/handle seams покрыты независимо (`tests/test_registry.py@bd997c7:1454-1467`, `tests/test_registry.py@bd997c7:1751-1832`).
7. Иерархия выше configured root остаётся доверенной предпосылкой. Direct root swap/final-path mismatch и все links внутри registry boundary проверяются, но отдельной native attack-пробы замены промежуточного ancestor выше root нет (`tests/test_registry.py@bd997c7:955-1054`).

## Проверка диапазона

- `git log c60d086..bd997c7`: один commit `bd997c7 fix: close remaining E1-S2 resolver findings`.
- `git diff --check c60d086..bd997c7`: успешно.
- Тесты и quality commands в рамках этого security handoff не запускались; оценены committed implementation и committed attack oracles целевого диапазона.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 0. **GATE_OK**
