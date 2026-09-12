# Выжимка
Роль: perf
Scope: `3bd870b..6089e28`
Вердикт: GATE_BLOCKED
Находки: 1 — P0: 0, P1: 1, P2: 0, P3: 0.

Предыдущие `PERF-E1-S2-001/002/003` остаются закрыты: корень, число попыток,
суммарные parser bytes, auxiliary entries/depth и итоговый отчёт по-прежнему
имеют численные пределы; чтение выполняется с проверенного handle, а YAML graph
не разворачивается. Новая проблема находится в component-wise resolver:
`MAX_LINK_COMPONENTS=256` ограничивает только одну текущую очередь, но не
суммарное число уже пройденных компонентов. Рекурсивная реализация перемножает
этот предел с `MAX_LINK_HOPS=40`, удерживает все промежуточные handles и suffix
tuples и достигает Python recursion limit либо process handle limit раньше
собственного численного предела.

# Отчёт

Проверены договор E1-S2 (`docs/phases/orchestrator-e1.md:107-151`), предыдущий
perf gate (`docs/handoff/e1-s2-rework-1-perf.md`) и дельта
`3bd870b..6089e28`. Анализ статический; код и тесты не запускались и не
изменялись.

Обозначения:

- `R=256` — `MAX_ROOT_ENTRIES`, `S=128` — `MAX_SKILLS`;
- `L=65_536` — `MAX_SKILL_FILE_BYTES`, `T=1_048_576` —
  `MAX_TOTAL_SKILL_BYTES`;
- `A=1_024` — `MAX_AUX_ENTRIES`, `D=32` — `MAX_AUX_DEPTH`;
- `H=40` — `_MAX_LINK_HOPS`, `C=256` — `_MAX_LINK_COMPONENTS`;
- `n<=R` — число корневых имён на normal path; overflow останавливается на
  `R+1`;
- `s<=S` — число уникальных каталогов, дошедших до попытки чтения `SKILL.md`;
- `e_j<=A` — число открытых auxiliary entries скилла `j`,
  `E=sum(e_j)<=S*A=131_072`;
- `U<=R+S+E=131_456` — число вызовов `open_child` за полную загрузку;
- `c_q<=C` — длина одной текущей очереди resolver; это не cumulative budget;
- `W<=(H+1)C=10_496` — число component visits одного Windows resolve в
  разрешённой текущими проверками верхней оценке; для POSIX initial child
  открывается напрямую, поэтому его link path не больше `HC`;
- `b_j` — принятые байты файла, `sum(b_j)<=T`; raw read одного attempt не
  больше `L+1`, а всех attempts — не больше
  `S(L+1)=8_388_736` байт;
- `P` — длина нормализуемого конечного пути, `K` — суммарная длина корневых
  имён, `Q` — строки, удерживаемые итоговыми `Skill`;
- `I` — число issues: `I<=R` на обработанном bounded-корне либо один
  root-level issue.

Wall time ниже предполагает завершение вызова файловой системы. При root на
сетевом mount/share ОС может задержать `resolve/open/stat/read/enumerate`
неограниченно долго: прикладного deadline нет. В коде нет HTTP-клиента,
фоновой сети или retry/backoff loop; после возврата системного вызова все
прикладные циклы имеют счётчик либо byte/entry bound.

## Complexity по всем путям

| Path | Вход / syscalls | O(time) | O(memory/resources), дополнительная | Числовой bound | Итог |
|---|---|---:|---:|---|---|
| Публичный фасад `registry.__init__` | 5 экспортов | `O(1)` | `O(1)` | Скрытие внутренних констант не снимает применяемые внутри budgets и не копирует данные | OK |
| Root `Path.resolve(strict=True)` | исходный путь, его компоненты/links | `O(P+links)` + metadata I/O | `O(P)` | Цикл становится `RuntimeError`; число hops/path ограничивает ОС, прикладных retries нет | OK |
| POSIX root open/check | `open(O_NOFOLLOW|O_NONBLOCK)`, `fstat`, 1 final-path readlink (до 2 с fallback) | `O(P)` + fixed call group | 1 root handle, `O(P)` | Финальный объект сверяется с already-resolved root; root handle закрывается context manager | OK |
| Windows root probe/open/check | 2×`CreateFileW`, fixed metadata/final-path calls | `O(P)` + fixed call group | не более 2 handles одновременно, `O(P)` | Probe закрывается; content handle повторно проверяется на reparse; retry отсутствует | OK |
| Root enumerate, normal | `n<=R`, POSIX streaming `scandir` / Win32 64 KiB batches | `O(K)` до sort | `O(n+K)` | Удерживается не более 256 имён | OK |
| Root enumerate, overflow | имя `R+1` | `O(K_257)` | `O(R+K_257)` | Остановка на 257-м имени, один issue, parser work отсутствует | OK |
| Root deterministic sort | `n<=R`, max name length `M` | `O(K+n log n*M)` worst-case comparisons | `O(n+K)` | Сортируется не более 256 ключей | OK |
| Safe report identifier | до `R` имён | `O(K)` total hashing/regex | `O(M)` transient | Не более одного SHA-256 на root entry | OK |
| Candidate identity/attempt dedup | уникальные directory IDs | `O(R)` average set work | `O(S)` | Из-за add-before-limit set кратко содержит не более 129 IDs; file attempts `<=128` | OK |
| POSIX direct plain child | `lstat`, `openat`, `fstat`, final-path readlink | `O(P)` + fixed calls | один result handle | Обычный непосредственный child не проходит component replay | OK |
| POSIX symlink resolution | до `H` link targets, каждая текущая очередь `<=C` | component syscalls `O(HC)`; tuple slicing/final-prefix paths `O(HC^2)` | до `O(HC)` одновременно открытых intermediate handles и stack depth; `O(HC^2)` живых tuple refs | До 10 240 component visits, но default recursion/fd limit достигается раньше | **BAD — PERF-E1-S2-004** |
| Windows plain component | probe `NtCreateFile`+metadata+close, затем content `NtCreateFile`+metadata/final path; fixed calls на component | `O(P)` на final-path materialization | один probe и один content handle на текущем шаге | Plain component открывается дважды, без retry | Локально OK; входит в finding по цепочке |
| Windows reparse component | probe, `DeviceIoControl`, parse, restart от root | `O(r+P)`, `r<=16_384` | `O(r+C)` на hop | Только symlink/mount-point tags; `H<=40` | Локально OK |
| Windows full component resolver | initial queue плюс до `H` target queues | до `O((H+1)C^2)` CPU из suffix slicing/prefix paths; `O((H+1)C)` component call groups | до `O((H+1)C)` handles/stack и `O((H+1)C^2)` tuple refs | `W<=10_496`; до `1_348_736` живых suffix references теоретически, прежде чем учесть tuple headers | **BAD — PERF-E1-S2-004** |
| Reparse buffer parser | header, fixed struct fields, UTF-16LE slice | `O(r)` | `O(r)` | `r<=16_384`; offsets/length/end проверяются, unsupported tag сразу отклоняется | OK |
| Link cycle / repeated targets | cycle длиной до `H+1` | входит в resolver rows | входит в resolver rows | 41-я попытка следования отклоняется; бесконечного link loop нет | Bounded, но ресурсно BAD из-за рекурсии |
| Component queue bound | текущий `components` tuple | `O(C)` на check/создание | `O(C)` для одной очереди | Проверяется `len(current_queue)<=256`, cumulative processed count отсутствует | **BAD — источник finding** |
| Final containment `_is_within` | `P` и все `path.parents` | `O(P^2)` из повторного `absolute/normpath` каждого prefix | `O(P)` transient | Физический target уже component-bounded относительно root, но проверка добавляется к каждому entry | Усиливает PERF-E1-S2-004 |
| Один resolver во всём load | `U<=131_456`, `W<=10_496` | до `U*W=1_379_762_176` component visits до постоянного множителя syscalls; string/tuple work выше | transient peak из resolver rows | Root/tree/report budgets не ограничивают cumulative components | **BAD — PERF-E1-S2-004** |
| `SKILL.md` open/type/containment | не более `S` attempts | resolver cost + fixed checks | один file handle поверх directory/root | Non-regular/escaped object не читается; тот же handle используется до close | OK вне resolver finding; прежний PERF-001 закрыт |
| `_read_up_to`, full/EOF | `r_j<=L+1` | `O(r_j)` для обычного full read | `O(r_j)` payload/chunks | Один payload `<=65_537`; handle закрывается | OK |
| `_read_up_to`, short reads | число вызовов `c_j<=L+1` | POSIX `O(r_j+c_j)`; Windows `ReadFile` buffer construction имеет консервативный `O(L^2)` cumulative allocation work при one-byte reads | peak `O(L+c_j)`; handle count constant | Не более 65 537 calls/attempt и 8 388 736 calls/load; это большой, но численно конечный byte-derived path, не unbounded retry | Bounded; без нового finding |
| Per-file overflow | oversized file | `O(L+1)` bytes/calls | `O(L)` | Parser не вызывается, причина `file_too_large` | OK |
| Aggregate byte overflow | `remaining < file` | `O(remaining+1)` | `O(remaining)` | Parser не вызывается; каждый такой attempt всё равно входит в `S` | OK |
| UTF-8/newline/document split | принятый `b_j` | `O(b_j)` | `O(b_j)` с постоянным множителем copies/line refs | Один файл `<=L`, сумма принятых inputs `<=T` | OK |
| YAML scan + `safe_load` | `y_j<=b_j` | два последовательных `O(y_j)` прохода | `O(y_j)` tokens/graph | Alias/anchor/tag отклоняются до construction; `sum(y_j)<=T` | OK; прежний parser bound сохранён |
| Metadata validation | scalar content внутри `b_j` | `O(b_j)` worst case | `O(1)` сверх YAML graph | `name<=64`, `description<=1024`; caption/body уже внутри `L` | OK |
| Duplicate canonical name | до `S` имён | `O(S)` average total | `O(S)` | `<=128`; duplicate не запускает auxiliary walk | OK |
| Auxiliary early `True` | первый regular file на позиции `k<=A` | `O(k)` entry work + resolver cost каждого open | `O(k+D)` identities/DFS плюс transient resolver | Немедленный возврат после первого допустимого файла | OK вне resolver finding |
| Auxiliary полный отрицательный | `e_j<=A`, depth `<=D` | `O(e_j)` enumeration + `e_j` resolver calls | `O(e_j+D)` visited/DFS плюс transient resolver | Не более 1024 opened entries и 1025 directory identities на skill | OK вне resolver finding; прежний PERF-002 закрыт |
| Auxiliary entry overflow | имя `A+1` | `O(A)` enumeration + resolver work первых `A` | `O(A+D)` | 1025-е имя отклоняется до open | OK |
| Auxiliary depth overflow | directory на depth `D+1` | `O(D)` DFS + resolver overflow child | `O(D)` ancestor handles плюс transient resolver | Depth 33 отклоняется, partial `Skill` не публикуется | OK вне resolver finding |
| Auxiliary cycle/shared directory | повтор identity | один open/resolve и average `O(1)` set lookup | `O(A)` visited | Повторный каталог не перечисляется; его open входит в `A` | OK |
| POSIX DFS resources | active depths `0..D` | входит в rows выше | порядка `D+1` directory handles и `D+1` scandir resources плюс root и transient resolver | Без resolver около `2(D+1)+1=67` descriptors/resources; с ним может добавиться до `HC` intermediate handles | **BAD только из-за PERF-E1-S2-004** |
| Windows DFS resources | active depths `0..D` | входит в rows выше | `D+1=33` buffers по 65 536 байт (2 162 688 байт), directory/root handles плюс transient resolver | Без resolver порядка 34–35 handles; с resolver теоретически ещё до 10 496 | **BAD только из-за PERF-E1-S2-004** |
| `Skill`/`LoadIssue` accumulation | `Q`, `I` | `O(Q+I)` | `Theta(Q+I)` — обязательный output | `Q=O(T+S*metadata)`, skills `<=128`, issues/log events `<=256` | OK; прежний PERF-003 закрыт |
| List → immutable tuples | skills `<=S`, issues `<=I` | `O(S+I)` | краткая копия `O(S+I)` refs | Не более 384 output refs поверх state lists | OK |
| Root-level failure | open/resolve/list/limit/`RuntimeError` | work до места отказа | один `LoadIssue` после unwind | Link resolver `RecursionError` является `RuntimeError`, поэтому весь накопленный report заменяется `root_unreadable`; handles закрываются unwind | Завершается, но подтверждает impact finding |
| Полный load | все bounded paths | `O(K+R log R+T+E+UHC^2)` текущая conservative bound | `O(T+R+S+A+D*buffer+HC^2 refs+HC handles)` | Все величины формально конечны, но `HC` не помещается в обычные stack/fd budgets, а aggregate component work достигает порядка миллиарда | **BAD — GATE_BLOCKED** |

Существенная разница с предыдущим perf gate: тогда один `open_child` был
оценён как один bounded path resolution. В новой дельте resolver реализует этот
путь сам и рекурсивно. Вызовы `_open_descendant(..., components[1:], ...)`
держат текущий `Handle` до возврата вложенного вызова
(`_posix_io.py:145-172`, `_windows_io.py:339-393`). Если ссылка встречается
после длинного префикса, `_follow_link`/`_follow_reparse_target` начинает новый
проход от root, не освобождая frames и handles префикса
(`_posix_io.py:120-141`, `_windows_io.py:410-443`). Проверка в
`_check_component_count` видит только новую очередь
(`_posix_io.py:201-203`, `_windows_io.py:719-721`).

При допустимой очереди из 256 компонентов уже несколько таких переходов
превышают стандартный Python recursion limit; до теоретических 40 hops код не
доходит. При повышенном recursion limit peak приближается к десяти тысячам
handles, что превышает типичный POSIX `RLIMIT_NOFILE`. Ошибка закрывает handles
при unwind, то есть постоянной утечки нет, но bounded вход способен вызвать
resource exhaustion и заменить весь ранее собранный результат root-level
ошибкой.

## Findings

### P1 — PERF-E1-S2-004 — Локальные link bounds перемножаются в stack и handles

Файлы:

- `src/skillhub/registry/_posix_io.py:120-203`;
- `src/skillhub/registry/_windows_io.py:329-443,714-721`;
- `src/skillhub/registry/_filesystem.py:234-305,341-347`.

`H=40` и `C=256` существуют, но `C` проверяет только длину очереди после
очередного link expansion. Число уже открытых/пройденных компонентов не
учитывается. Рекурсивный descent удерживает parent handle и каждый suffix tuple
до возврата. Поэтому один разрешённый вход имеет до `10_496` component visits,
`O(HC)` simultaneous handles/frames и `O(HC^2)` живых tuple references. На
полную загрузку root/skill/tree budgets допускают до `131_456` resolver calls,
то есть формальный верхний предел порядка `1.38` млрд component visits до
постоянного множителя нативных вызовов. Повторная нормализация каждого
`path.parent` в `_is_within` добавляет `O(P^2)` строковой работы.

Конкретное исправление:

1. Переписать component resolver на итеративный цикл с deque/index вместо
   рекурсии и `components[1:]`.
2. Ввести один cumulative budget `components_processed<=256` на всю операцию,
   включая компоненты до и после каждой ссылки; `H<=40` оставить отдельной
   защитой от циклов.
3. Одновременно владеть не более чем одним промежуточным directory handle:
   при переходе к следующему компоненту закрывать предыдущий owned handle, а
   при restart от root закрывать весь owned prefix. Переданные root/parent
   handles не закрывать.
4. На Windows обычный непосредственный child сначала открывать относительно
   переданного `parent`; replay от root выполнять только после разбора link
   target. Это убирает повторное прохождение ancestor path для каждого
   auxiliary entry.
5. Заменить генератор с нормализацией каждого parent на одну нормализацию и
   `commonpath/relative_to`, чтобы containment был `O(P)`.
6. Превышение cumulative component/hop budget переводить в существующий
   стабильный `unsafe_path`; никаких retries не добавлять.

Целевая оценка одного resolve после исправления:
`O(C+H)` component work, `O(C)` queue memory, `O(1)` owned intermediate
handles и `O(1)` Python stack при `C=256`, `H=40`. Полная загрузка тогда имеет
верхнюю оценку `O(K+R log R+T+E+U(C+H))` и сохраняет существующие
root/entry/tree/report budgets.

Итого: P0 — 0, P1 — 1, P2 — 0, P3 — 0.
