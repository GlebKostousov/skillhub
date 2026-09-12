# Выжимка

Роль: security
Scope: `3cadabe..eab1abf`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Дельта закрывает блокеры rework-3 без ослабления прежних гарантий. Для обоих
поддерживаемых reparse tags теперь до декодирования проверяются границы и
двухбайтовое выравнивание `SubstituteName` и `PrintName`. Абсолютная UNC-цель
допускается только после нормализации и containment в том же доверенном
UNC-root; переход local → UNC, другой share и device namespace отклоняются до
любого открытия цели. Допустимая цель затем открывается покомпонентно от
удерживаемого root handle.

Root no-follow, held-parent relative open, повторная проверка raced reparse
token, final-handle containment и очистка дескрипторов не регрессировали.

# Отчёт

## Договор, rework-3 и delta

- Договор E1-S2 требует bounded input, safe YAML, строгий UTF-8, изоляцию
  дефектного каталога, запрет чтения внешней symlink-цели и безопасный
  относительный warning (`docs/phases/orchestrator-e1.md:107-151`).
- Security rework-3 дал 0 findings, но reviewer обнаружил отказ эквивалентной
  абсолютной внутренней UNC-цели и мёртвый альтернативный вход resolver
  (`docs/handoff/e1-s2-rework-3-security.md:1-14`;
  `docs/handoff/e1-s2-rework-3-reviewer.md:20-50`). QA отдельно воспроизвёл
  принятие нечётного `SubstituteNameOffset`
  (`docs/handoff/e1-s2-rework-3-qa-tester.md:75-100`).
- В `3cadabe..eab1abf` один commit
  `eab1abf fix: close fourth E1-S2 rework findings`. Production-логика
  изменена только в чистом reparse parser/normalizer и удалением
  неиспользуемого входа resolver; остальные четыре production-файла меняют
  только русскую терминологию docstring. Публичный API, зависимости,
  YAML/parser policy и HTTP/LLM surface не менялись.

## Фактический security-flow

1. `SkillRegistry.load()` строго разрешает настроенный root и передаёт
   ожидаемый путь в `open_registry()`; ожидаемый отказ становится одним
   безопасным root issue (`src/skillhub/registry/_registry.py:42-53`,
   `src/skillhub/registry/_registry.py:131-157`).
2. Windows открывает root с `FILE_FLAG_OPEN_REPARSE_POINT`: probe и content
   token проверяются на reparse, после чего тип и `final_path` удерживаемого
   content handle сверяются с ожидаемым root до enumeration
   (`src/skillhub/registry/_windows_handles.py:43-57`,
   `src/skillhub/registry/_windows_handles.py:83-121`;
   `src/skillhub/registry/_filesystem.py:167-193`).
3. Каталог перечисляется через тот же root token. Candidate, `SKILL.md` и
   auxiliary entries открываются относительно удерживаемого parent handle
   (`src/skillhub/registry/_filesystem.py:68-105`,
   `src/skillhub/registry/_filesystem.py:139-162`;
   `src/skillhub/registry/_windows_resolver.py:31-60`).
4. Child probe использует attribute-only `NtCreateFile` с
   `OBJECT_ATTRIBUTES.root_directory=parent.token` и
   `FILE_OPEN_REPARSE_POINT`. Content open повторяет no-follow; если объект
   стал reparse между двумя открытиями, буфер читается с уже удерживаемого
   второго token, token закрывается, а цель проходит тот же parser/policy
   (`src/skillhub/registry/_windows_handles.py:125-176`;
   `src/skillhub/registry/_windows_resolver.py:71-102`).
5. Parser принимает только mount point и symlink, ограничивает структуру
   объявленным `ReparseDataLength`, валидирует оба диапазона имён и декодирует
   непустой `SubstituteName` как строгий UTF-16LE
   (`src/skillhub/registry/_windows_reparse.py:17-33`,
   `src/skillhub/registry/_windows_reparse.py:69-151`).
6. Относительная цель сначала привязывается к `parent.final_path`; абсолютная
   цель нормализуется из разрешённой local/UNC-формы. Затем `abspath`,
   `normcase` и `commonpath` сравнивают её с `root.final_path`
   (`src/skillhub/registry/_windows_reparse.py:36-66`,
   `src/skillhub/registry/_windows_reparse.py:155-209`).
7. Только после успешной policy цель превращается в компоненты, очередь
   перезапускается от удерживаемого root, и каждый компонент снова открывается
   no-follow. Pathname UNC-цели для follow-open не используется
   (`src/skillhub/registry/_windows_resolver.py:104-121`,
   `src/skillhub/registry/_windows_handles.py:140-176`).
8. Полученный handle проходит final-path containment и type check до bounded
   read; внешний directory handle не перечисляется
   (`src/skillhub/registry/_filesystem.py:195-228`,
   `src/skillhub/registry/_filesystem.py:260-310`).
9. Только после файловой границы применяются strict UTF-8, запрет YAML
   graph/tag, `safe_load`, metadata/body validation, duplicate first-wins и
   bounded `has_files`; issue/log не включает body, абсолютный путь или
   traceback (`src/skillhub/registry/_document.py:18-126`;
   `src/skillhub/registry/_registry.py:70-129`,
   `src/skillhub/registry/_registry.py:150-157`).

## Reparse buffer: tags, bounds и alignment

- Header требует минимум 8 bytes; `8 + ReparseDataLength` не может выходить за
  фактически полученный буфер. Неизвестный tag отклоняется без parser dispatch
  (`src/skillhub/registry/_windows_reparse.py:17-33`,
  `src/skillhub/registry/_windows_reparse.py:69-76`).
- Mount point требует полный 16-byte fixed prefix, symlink — 20-byte prefix с
  `Flags`. Поэтому оба `struct.unpack_from()` выполняются только внутри
  объявленного диапазона (`src/skillhub/registry/_windows_reparse.py:79-114`).
- Для **обоих tags** и **обоих имён** перед slice проверяются чётность offset,
  чётность byte length и `offset + length <= PathBuffer length`. Сложение
  выполняется Python integer без wraparound
  (`src/skillhub/registry/_windows_reparse.py:86-109`,
  `src/skillhub/registry/_windows_reparse.py:136-144`).
- Декодируется только доступный для открытия `SubstituteName`; нечётная длина,
  нечётный offset, выход за границу, invalid UTF-16 и пустая цель завершаются
  `UnsafePathError`. Неиспользуемый `PrintName` не декодируется, но его
  диапазон и alignment больше нельзя использовать для структурно
  противоречивого буфера (`src/skillhub/registry/_windows_reparse.py:90-133`).
- Regression-oracles включают odd offset для mount point и symlink, а также
  odd offset, odd length и out-of-range `PrintName` отдельно для обоих tags
  (`tests/test_registry.py@eab1abf:1827-1878`).

Подтверждённого out-of-bounds read, misaligned UTF-16 slice или обхода parser
через второй поддерживаемый tag не найдено.

## UNC normalization, containment и pre-follow

Policy разделяет доверенный root и недоверенную строку из reparse buffer:

- `\??\UNC\server\share\...` нормализуется в
  `\\server\share\...`; обычная UNC-форма сохраняется
  (`src/skillhub/registry/_windows_reparse.py:168-184`).
- При local root UNC candidate имеет другой anchor, поэтому `commonpath`
  завершается `ValueError` и цель отклоняется. При UNC-root другой server/share
  также имеет другой anchor; malformed server-only UNC не проходит
  containment (`src/skillhub/registry/_windows_reparse.py:55-66`;
  `tests/test_registry.py@eab1abf:1475-1535`).
- `\Device\`, `\\.\`, `\??\GLOBALROOT`, `\\?\GLOBALROOT` и
  `\\?\UNC` отклоняются как device namespace. Local absolute target другого
  drive и любая local/UNC target вне root отклоняются тем же containment до
  открытия цели (`src/skillhub/registry/_windows_reparse.py:186-209`;
  `tests/test_registry.py@eab1abf:1496-1509`,
  `tests/test_registry.py@eab1abf:1908-1949`).
- Абсолютные `\??\UNC\server\share\skills\shared` и
  `\\server\share\skills\shared` при root
  `\\server\share\skills` становятся `("shared", ...)` и открываются от
  удерживаемого root, а не по сетевому pathname
  (`tests/test_registry.py@eab1abf:1590-1654`,
  `tests/test_registry.py@eab1abf:1881-1905`).
- Запрещённые cases используют oracle, в котором любой content
  `_open_relative()` является ошибкой; все они завершаются до такого вызова.
  Открывается только сама reparse point с no-follow для чтения её буфера
  (`tests/test_registry.py@eab1abf:1475-1535`).

Таким образом, same trusted-root UNC сохраняет внутреннюю link-семантику, но
local → UNC, другой share и device namespace не создают исходящий follow-open.

## No-follow, root и handles: отсутствие регрессии

- Root probe/content по-прежнему используют `FILE_FLAG_OPEN_REPARSE_POINT`;
  relative probe/content — `FILE_OPEN_REPARSE_POINT`. В delta эти executable
  ветки не менялись (`src/skillhub/registry/_windows_handles.py:83-121`,
  `src/skillhub/registry/_windows_handles.py:125-176`;
  `tests/test_registry.py@eab1abf:1752-1825`,
  `tests/test_registry.py@eab1abf:1952-1988`).
- `root.final_path` проверяется до enumeration; candidate/file containment
  проверяет именно открытый handle до read/enumeration. Прежние root swap,
  path mismatch и external handle attack-oracles остаются в suite
  (`src/skillhub/registry/_filesystem.py:180-193`,
  `src/skillhub/registry/_filesystem.py:195-228`,
  `src/skillhub/registry/_filesystem.py:279-310`;
  `tests/test_registry.py@eab1abf:960-1102`,
  `tests/test_registry.py@eab1abf:1187-1257`).
- Probe token закрывается в `finally`; raced reparse token закрывается перед
  restart; не-final plain handle становится единственным owned parent и
  закрывается при замене или в resolver `finally`. Root/initial parent не
  закрываются resolver-ом (`src/skillhub/registry/_windows_handles.py:125-137`;
  `src/skillhub/registry/_windows_resolver.py:46-60`,
  `src/skillhub/registry/_windows_resolver.py:89-102`,
  `src/skillhub/registry/_windows_resolver.py:124-160`).
- Удалённый `_open_components()` был неиспользуемым вторым входом. Единственный
  production-вход `open_child()` создаёт один cumulative `ResolutionBudget`;
  общий предел 256 component/link steps и 40 link hops сохранён
  (`src/skillhub/registry/_windows_resolver.py:31-69`;
  `src/skillhub/registry/_resolution.py:6-43`;
  `tests/test_registry.py@eab1abf:1657-1748`).
- Fault-injection продолжает проверять закрытие root/child при metadata error,
  raced content token и неверного промежуточного типа
  (`tests/test_registry.py@eab1abf:2038-2144`).

Нового pathname check-then-use, external pre-follow или очевидной ветки утечки
дескриптора в целевом диапазоне не найдено.

## A–I

### A. Input validation

Сохраняются file/total byte bounds, strict UTF-8, front matter, mapping,
kebab-case/length, description/caption и непустое body. YAML aliases, anchors
и tags запрещаются до `safe_load`; ожидаемые constructor exceptions
изолируются как `invalid_yaml` (`src/skillhub/registry/_document.py:18-126`;
`src/skillhub/registry/_limits.py:1-19`). Новая бинарная граница fail-closed
проверяет header, tag, fixed prefix, оба name ranges/alignment, UTF-16 и
непустой substitute target.

### B. Authz / tenant

N/A: users, tenants и прикладной authorization surface отсутствуют. Контроль
filesystem trust boundary рассмотрен в D/G.

### C. Secrets / logs

Недоверенное имя заменяется bounded SHA-256 identifier; warning и `LoadIssue`
содержат только safe path и стабильную причину. Payload, исходное hostile name,
absolute path и traceback не публикуются
(`src/skillhub/registry/_registry.py:70-89`,
`src/skillhub/registry/_registry.py:122-129`,
`src/skillhub/registry/_registry.py:150-157`).

### D. Injection / files

Unsafe YAML object construction закрыта. Root/child I/O привязан к handles;
reparse point читается no-follow, а её target проходит binary validation,
namespace policy и containment до follow-open. Внутренняя UNC-цель не создаёт
pathname-open: она переигрывается компонентами от root handle. File injection,
external traversal или сетевого pre-follow в delta не найдено.

### E. Crypto

N/A: криптографического протокола, ключей, токенов и подписей нет. SHA-256
используется только для необратимого безопасного идентификатора пути
(`src/skillhub/registry/_registry.py:122-129`).

### F. HTTP / network

N/A для HTTP: endpoints, cookies, CORS, CSRF и сетевой клиент отсутствуют.
Filesystem-induced SMB surface проверена отдельно: внешняя UNC target
отклоняется до follow-open; сеть разрешается только через явно настроенный
доверенный UNC-root и relative handle I/O
(`src/skillhub/registry/_windows_reparse.py:55-66`,
`src/skillhub/registry/_windows_reparse.py:168-198`;
`tests/test_registry.py@eab1abf:1475-1654`).

### G. Replay / integrity

Открытый root связан с ожидаемым final path до enumeration; final child handle
проверяется до использования. Stable sort, identity cycle guard и duplicate
first-wins сохраняют детерминированность
(`src/skillhub/registry/_filesystem.py:68-82`,
`src/skillhub/registry/_filesystem.py:145-158`,
`src/skillhub/registry/_filesystem.py:187-193`,
`src/skillhub/registry/_filesystem.py:279-304`;
`src/skillhub/registry/_registry.py:91-119`).

### H. DoS / bounds

Ограничены root entries, skill attempts, file/total bytes, auxiliary
entries/depth, reparse buffer, cumulative resolution steps и link hops
(`src/skillhub/registry/_limits.py:1-19`,
`src/skillhub/registry/_windows_abi.py:17-25`,
`src/skillhub/registry/_resolution.py:6-43`). Resolver итеративен и владеет не
более чем одним intermediate handle; прикладных retries нет.

### I. Stored XSS / data exposure

HTML/DOM sink в слайсе отсутствует; metadata/body остаются недоверенными
строками для будущего контекстного кодирования. External file handle
отклоняется до read, external directory handle — до enumeration; внешнее
содержимое не попадает в `Skill`, issue или log
(`src/skillhub/registry/_filesystem.py:195-228`,
`src/skillhub/registry/_filesystem.py:260-310`).

### J. LLM

N/A: LLM-scope **НЕТ**. Prompt, model output, agent/tool execution и dynamic
code loading отсутствуют; `SKILL.md` валидируется и каталогизируется только как
данные (`docs/phases/orchestrator-e1.md:107-145`).

## Attack-test gaps

Ниже только неблокирующие пробелы доказательной базы; подтверждённой
уязвимости за ними не найдено.

1. Same-share UNC и запрет внешней UNC проверены deterministic capability
   seams, но не настоящим SMB share и не packet-level oracle отсутствия
   исходящего соединения.
2. Malformed-buffer tests покрывают обе структуры и оба name range, но не
   перебирают все семантически бесполезные мутации: нечётный неиспользуемый
   хвост `PathBuffer`, overlap двух валидных диапазонов, unknown symlink flag и
   invalid UTF-16 только в неиспользуемом `PrintName`. Эти формы не меняют
   bounded/aligned slice открываемого `SubstituteName`, но отдельного
   fail-closed oracle для них нет.
3. Device spellings не перечислены исчерпывающе. Проверены основные NT/Win32
   формы; остальные по-прежнему должны пройти drive/anchor containment, а
   target pathname никогда не открывается напрямую.
4. Реальная privileged внутренняя UNC symlink/junction не закреплена e2e:
   normalizer и held-root relative-open проверены отдельными deterministic
   oracles.
5. Cleanup подтверждён fault injection и cumulative resolver oracle, но нет
   process handle-count soak на длинной серии реальных Windows link chains.
6. Иерархия выше configured root остаётся доверенной предпосылкой. Direct root
   reparse swap/final-path mismatch и все links внутри registry boundary
   закрыты; отдельной native атакующей пробы замены промежуточного ancestor
   выше root нет.

## Проверка диапазона

- `git merge-base 3cadabe eab1abf` → `3cadabe`; диапазон содержит ровно один
  commit `eab1abf`.
- `git diff --check 3cadabe..eab1abf` → успешно.
- Код и тесты не изменялись и не запускались по условию re-gate; оценены
  committed implementation и committed attack-oracles целевого диапазона.
- Записан только `docs/handoff/e1-s2-rework-4-security.md`; commit не создавался.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 0. **GATE_OK**
