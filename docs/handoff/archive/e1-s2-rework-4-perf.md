# Выжимка

Роль: perf
Scope: `3cadabe..eab1abf`
Вердикт: `GATE_OK`
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Изменения reparse/UNC сохраняют предел нативного буфера 16 384 байта,
containment относительно доверенного root, общий resolver-budget 256 шагов и
40 переходов по ссылкам. Resolver остаётся итеративным, держит константную
глубину Python stack и не более двух собственных Windows handles. Пределы
корневого списка, попыток, байтов, auxiliary traversal и итогового отчёта не
изменены.

# Отчёт

Проверены договор E1-S2 (`docs/phases/orchestrator-e1.md:107-151`), предыдущий
perf gate (`docs/handoff/e1-s2-rework-3-perf.md`) и committed delta
`3cadabe..eab1abf`. Диапазон содержит один коммит `eab1abf`. Анализ
статический; тесты не запускались. Незакоммиченные изменения рабочей копии в
scope не включались.

Обозначения:

- `R=256` — `MAX_ROOT_ENTRIES`, `S=128` — `MAX_SKILLS`;
- `L=65_536` — `MAX_SKILL_FILE_BYTES`, `T=1_048_576` —
  `MAX_TOTAL_SKILL_BYTES`;
- `A=1_024` — `MAX_AUX_ENTRIES`, `D=32` — `MAX_AUX_DEPTH`;
- `C=256` — `MAX_RESOLUTION_STEPS`, `H=40` — `MAX_LINK_HOPS`;
- `Z=16_384` — `_MAX_REPARSE_BYTES`, `B=65_536` —
  `_DIRECTORY_BUFFER_BYTES`;
- `Y<=Z/2=8_192` — консервативный предел символов одной декодированной
  UTF-16 reparse-target; реальные mount-point/symlink пределы меньше из-за
  заголовка;
- `P` — максимальная длина материализованного root/final path; исходный root
  ограничен Windows path API, а одна reparse-target добавляет не больше `Y`;
- `n<=R` — число удерживаемых корневых имён; overflow определяется на
  `R+1`;
- `s<=S` — число каталогов, дошедших до чтения `SKILL.md`;
- `e_j<=A` — число открытых auxiliary entries скилла `j`,
  `E=sum(e_j)<=S*A=131_072`;
- `U<=R+S+E=131_456` — число `open_child` за одну полную загрузку;
- `K` — суммарная длина удерживаемых корневых имён, `Q` — строки итоговых
  `Skill`, `I<=R` — число локальных issues либо один root issue.

Wall time предполагает возврат системных вызовов. Для явно настроенного
сетевого root прикладного deadline нет, но retry/backoff loop и фоновая
работа отсутствуют.

## Reparse buffer, UNC и путь

`_read_reparse_data` всегда выделяет ровно `Z=16_384` байта и возвращает не
больше `Z` байт (`_windows_abi.py:19-22`,
`_windows_handles.py:211-225`). Поле `data_length` проверяется до чтения
вложенных полей. Для mount point и symlink обе пары
`SubstituteNameOffset/Length` и `PrintNameOffset/Length` проверяются на
UTF-16-выравнивание и попадание в `data_end`; только после этого выполняется
ограниченный slice/decode (`_windows_reparse.py:69-152`). Значения `uint16`
не могут вызвать переполнение Python integer или выделение по заявленной,
но отсутствующей длине.

Новая UNC-ветка делает один `casefold`, одну prefix-нормализацию и затем
проходит тот же `abspath/normcase/commonpath/relpath`, что и локальная
абсолютная цель (`_windows_reparse.py:36-66,156-208`). Поэтому:

1. `\??\UNC\server\share\...` и обычная `\\server\share\...` не открываются
   во время разбора и принимаются только при совпадении share и containment
   внутри configured root.
2. Другой share, локальный root против UNC, путь вне root и несовместимый
   drive завершаются до постановки компонентов в resolver.
3. Device/extended-device формы по-прежнему отклоняются.
4. Одновременно материализуется `O(P+Y)` символов; цели разных hops не
   накапливаются в одну строку.

После `relpath` staging tuple может кратко содержать до `O(Y+C)` ссылок на
компоненты, но до обхода вызывается `require_pending`. Принятая очередь всегда
удовлетворяет `steps + len(pending) <= C`; oversized target не проходит ни
одного дополнительного filesystem component
(`_windows_resolver.py:108-121`). Таким образом, buffer/path staging имеет
жёсткий предел `Z`, а фактический component traversal — `C`.

## Resolver budget, stack и handles

Один `ResolutionBudget` создаётся только в `open_child`; альтернативный
неиспользуемый вход удалён (`_windows_resolver.py:31-51`). На каждой итерации:

- `visit_component()` расходует один общий step;
- успешный `follow_link()` расходует ещё один тот же step и один из `H` hops;
- replacement queue проверяется против оставшихся steps до следующей
  итерации.

Следовательно, `component_visits + successful_link_hops = steps <=256`, а
успешных hops не больше 40. Ровно 40 hops допустимы только при наличии общего
step budget; 41-й отклоняется до инкремента hop. Цикл `while state.pending`
не рекурсивен, поэтому resolver stack — `O(1)`.

State владеет максимум одним intermediate parent. Probe закрывается до
content-open; raced content token закрывается до requeue; замена parent
закрывает прежний owned handle; общий `finally` закрывает последний owned
parent. Пиковое приращение resolver относительно заимствованных `root/parent`
— два handles: owned parent плюс текущий probe/content/result
(`_windows_handles.py:125-137,180-196`,
`_windows_resolver.py:52-60,72-160`). После ошибки приращение равно нулю,
после успеха вызывающему остаётся один result handle.

## Complexity по всем путям

| Path | O(time) | O(memory/resources), дополнительная | Жёсткий bound / исход |
|---|---:|---:|---|
| Root `Path.resolve(strict=True)` | `O(P+links)` + metadata I/O | `O(P)` | Ошибка/cycle превращается в один root issue; retries нет |
| Windows root probe/open/check | `O(P)` + fixed native calls | не более одного probe/content handle до публикации root | Probe и error path закрыты |
| Root enumerate, normal | `O(K)` до sort | `O(n+K)` + один `B` buffer | `n<=256` |
| Root enumerate, overflow | `O(K_257)` | `O(R+K_257+B)` | остановка на 257-м имени, один root issue |
| Root deterministic sort | `O(K+n log n*M)` | `O(n+K)` | сортируется не более 256 имён |
| Safe report identifier | `O(K)` total | `O(M)` transient | не более одного SHA-256 на root entry |
| Candidate identity/attempt dedup | `O(R)` average | `O(S)` | identities кратко `<=129`, file attempts `<=128` |
| Reparse native read | fixed IOCTL + `O(Z)` copy | `O(Z)` native buffer + `O(Z)` bytes result | `bytes_returned<=16_384` |
| Reparse header / unsupported tag | `O(1)` | `O(1)` | minimum header и `data_end<=len(data)` до parser dispatch |
| Mount-point parse | `O(Y)` decode | `O(Y)` | обе UTF-16 ranges внутри `data_end`, `Y<=8_192` |
| Symlink parse | `O(Y)` decode | `O(Y)` | обе UTF-16 ranges внутри `data_end`, `Y<=8_192` |
| Malformed substitute/print range | `O(1)` | `O(1)` | отказ до slice/decode |
| Relative reparse target | `O(P+Y)` | `O(P+Y)` | absolute/device форма отклоняется; затем общий containment |
| Local absolute target | `O(P+Y)` | `O(P+Y)` | только drive-absolute; другой drive/outside root отклоняется |
| Internal absolute UNC target | `O(P+Y)` | `O(P+Y)` | только тот же share и путь внутри configured UNC root |
| External/malformed UNC or device target | `O(P+Y)` worst case | `O(P+Y)` transient | отказ до filesystem follow-open |
| Target component staging | `O(P+Y+C)` | transient `O(P+Y+C)` refs/chars | staging `<=O(Y+C)`; принятая queue помещается в остаток `C` |
| Windows plain component | fixed probe/content/metadata + `O(P)` final path | owned parent + один probe/content/result | не более двух собственных handles |
| Windows probed reparse component | fixed probe/IOCTL + `O(Z+P+Y+C)` | `O(Z+P+Y+C)`, owned parent | probe закрыт до parse/requeue |
| Windows raced reparse component | fixed probe/content/IOCTL + `O(Z+P+Y+C)` | `O(Z+P+Y+C)`, owned parent + raced token | raced token закрыт до parse/requeue |
| Requeue accepted | `O(P+Y+C)` | новая queue `O(C)`, transient `O(Y+C)` | `steps+pending<=256` |
| Requeue rejected | `O(P+Y+C)` | transient `O(Y+C)` | oversized queue не обходится |
| Target resolves to held root/parent | fixed duplicate-open + metadata `O(P)` | один result token | выполняется не более одного раза в конце resolve |
| Один полный Windows resolver | `O(C*P+H*(Z+P+Y+C))` CPU, `O(C)` fixed syscall groups | `O(Z+P+Y+C)`, stack `O(1)`, handles `<=2` | `visits+hops<=256`, `hops<=40`; accepted queue-copy work `O(H*C)` |
| POSIX resolver, unchanged | `O(C*P+H*(P+Y+C))` | `O(P+Y+C)`, stack `O(1)`, descriptors `<=2` | тот же общий `C/H` budget |
| Все resolver calls одной загрузки | `O(U*(C*P+H*(Z+P+Y+C)))` | один resolver active за раз | steps `<=33_652_736`, hops `<=5_258_240` |
| Final containment | `O(P)` | `O(P)` transient | одна нормализация пары и `commonpath` |
| `SKILL.md` open/type/read guard | resolver + fixed checks | один file handle поверх root/directory | не более 128 attempts; non-regular не читается |
| `_read_up_to`, full/short | POSIX `O(L)`; Windows worst-case `O(L^2)` allocation work при one-byte reads | peak `O(L)` bytes/objects | не больше `L+1` bytes/calls на попытку |
| Per-file/aggregate byte overflow | `O(min(L,remaining)+1)` | `O(L)` | parser не вызывается; `sum(payload)<=T` |
| UTF-8/front matter/YAML/validation | `O(b_j)` на последовательный проход | `O(b_j)` transient | `sum(b_j)<=T`; unsafe YAML отклоняется |
| Auxiliary early success | `O(k*C)` component I/O | `O(k+D)` identities/DFS + resolver transient | `k<=A`, возврат на первом regular file |
| Auxiliary full negative | `O(e_j*C)` component I/O | `O(A+D)` + iterator buffers | `e_j<=1_024`, visited identities `<=1_025`, depth `<=32` |
| Auxiliary entry overflow | работа первых `A` entries | `O(A+D)` | 1025-е имя учитывается, но не открывается |
| Auxiliary depth overflow | работа первых `D` levels | `O(D)` stack/handles | directory depth 33 закрывается при unwind |
| Auxiliary cycle/shared directory | один bounded open + average `O(1)` lookup | входит в visited `O(A)` | повторная identity не перечисляется |
| POSIX DFS resources | входит в bounded traversal | `O(D)` handles/scandir; консервативно около 69 fd/resources с resolver | unwind/return/exception закрывают ресурсы |
| Windows DFS resources | входит в bounded traversal | handles `<=36`, buffers `<=33*B=2_162_688` bytes | один enumeration buffer на активный depth |
| `Skill`/`LoadIssue` accumulation | `O(Q+I)` | `Theta(Q+I)` — обязательный output | skills `<=128`, issues/log events `<=256`, `Q=O(T)` |
| Lists → immutable tuples | `O(S+I)` | краткая копия `O(S+I)` refs | не более 384 output refs |
| Полная загрузка | `O(K+R log R+T+E+U*(C*P+H*(Z+P+Y+C)))` | `O(T+R+S+A+D*B+C+P+Z)` | все прикладные counters, bytes, traversal stack и handles ограничены |

Принятие внутренней абсолютной UNC-target не добавляет новый обход: после
строковой проверки она превращается в root-relative components и проходит
тот же `ResolutionBudget`. Root/share не копятся между hops, recursive resolver
не возвращён, дополнительный handle не удерживается.

Корневой список по-прежнему останавливается на 257-м имени. Auxiliary DFS
потоковый, открывает не больше 1 024 entries одного скилла, ограничен depth 32
и дедуплицирует directory identity (`_filesystem.py:68-82,232-339`). Итоговый
report удерживает не больше 128 skills и 256 локальных issues; root failure
заменяет результат одним issue (`_registry.py:42-66,84-88,147-157`).

Дополнительное исправление не требуется.

## Findings

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.
