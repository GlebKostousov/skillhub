# Выжимка
Роль: perf
Scope: `c60d086..bd997c7`
Вердикт: GATE_OK
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

`PERF-E1-S2-004` закрыт. POSIX- и Windows-resolver теперь используют один
итеративный цикл, общий накопительный бюджет и не удерживают цепочку
промежуточных handles. За один resolve сумма посещений компонентов и учтённых
переходов по ссылкам не превышает 256, переходов по ссылкам не больше 40,
Python stack имеет константную глубину, а одновременно созданных resolver-ом
handles не больше двух. Прежние `PERF-E1-S2-001/002/003` остаются закрыты:
пределы корня, попыток, байтов, обхода и отчёта сохранены.

# Отчёт

Проверены договор E1-S2 (`docs/phases/orchestrator-e1.md:107-151`), предыдущий
perf gate (`docs/handoff/e1-s2-rework-2-perf.md`) и дельта
`c60d086..bd997c7`. Анализ статический; код и тесты не запускались и не
изменялись.

Обозначения:

- `R=256` — `MAX_ROOT_ENTRIES`, `S=128` — `MAX_SKILLS`;
- `L=65_536` — `MAX_SKILL_FILE_BYTES`, `T=1_048_576` —
  `MAX_TOTAL_SKILL_BYTES`;
- `A=1_024` — `MAX_AUX_ENTRIES`, `D=32` — `MAX_AUX_DEPTH`;
- `C=256` — `MAX_RESOLUTION_STEPS`, `H=40` — `MAX_LINK_HOPS`;
- `Z=16_384` — Windows reparse buffer, `B=65_536` — Windows directory
  buffer;
- `Y` — длина одной строки link target; на Windows она ограничена `Z`, на
  POSIX — пределом symlink target файловой системы;
- `n<=R` — число удерживаемых корневых имён; overflow обнаруживается на
  `R+1`;
- `s<=S` — число уникальных каталогов, дошедших до чтения `SKILL.md`;
- `e_j<=A` — число открытых auxiliary entries скилла `j`,
  `E=sum(e_j)<=S*A=131_072`;
- `U<=R+S+E=131_456` — консервативный предел всех `open_child` за load;
- `P` — максимальная длина материализуемого конечного пути, `K` — суммарная
  длина корневых имён, `Q` — строки итоговых `Skill`;
- `I<=R` — число issues на обработанном bounded-корне либо один root issue.

Wall time предполагает завершение системных вызовов. Для сетевого
mount/share прикладного deadline нет, но retry/backoff loop и фоновой сети
тоже нет: после возврата ОС все циклы имеют численный предел.

## Доказательство общего resolver budget

`ResolutionBudget` хранит только `steps` и `link_hops`
(`_resolution.py:15-44`). На входе каждой итерации resolver выполняется
инвариант `steps + len(pending) <= C`:

1. Перед первым циклом `require_pending` проверяет исходную очередь
   (`_posix_io.py:149-155`, `_windows_resolver.py:72-78`).
2. Обычный шаг удаляет один компонент и вызывает `visit_component`: длина
   очереди уменьшается на один, `steps` растёт на один, сумма не растёт.
3. Ссылка сначала расходует посещение компонента, затем `follow_link`
   расходует ещё один общий step. Новая очередь проверяется через
   `require_pending` до следующей итерации
   (`_posix_io.py:158-181`, `_windows_resolver.py:81-130`).
4. Каждая итерация увеличивает `steps` минимум на один, а `_consume_step`
   отвергает попытку после 256. Поэтому component visits не больше 256, а
   точнее `visits + charged_link_hops = steps <= 256`.
5. `follow_link` проверяет `link_hops >= 40` до инкремента. Разрешены ровно
   40 успешных переходов; 41-й отвергается. Посещение компонента 41-й ссылки
   уже входит в общий step, сам переход — нет.

Оба resolver-а держат одну `deque` и выполняют `while state.pending`, без
самовызова (`_posix_io.py:129-167`, `_windows_resolver.py:55-95`).
Следовательно, глубина Python stack — `O(1)`. Очередь и её последовательные
копии после проверки имеют peak `O(C)` component references. До проверки
очереди один разобранный target может кратко добавить `O(Y)` references.
Суммарная длина принятых replacement queues не больше
`H*C=10_240`; постоянное число tuple/deque-копий даёт `O(H*C)` reference
work, но не возвращает прежние `O(H*C)` одновременные frames/handles или
`O(H*C^2)` живые suffix references.

## Handles и очистка

- POSIX: переданные `root` и `initial_parent` заимствованы. Состояние владеет
  максимум одним `owned_parent`; при открытии следующего компонента старый и
  новый handle кратко сосуществуют, после чего `_replace_parent` закрывает
  старый. При restart от root prefix закрывается. На любом исключении
  `finally: _close_owned(state)` закрывает промежуточный handle
  (`_posix_io.py:129-138,184-217`). Ошибка метаданных закрывает только что
  открытый descriptor (`_posix_io.py:241-255`). Успех оставляет вызывающему
  один итоговый handle.
- Windows: attribute-only probe всегда закрывается в `finally`; content token
  закрывается при reparse race и при ошибке metadata/final-path. Состояние
  также владеет максимум одним prefix handle, а общий `finally` закрывает его
  (`_windows_handles.py:125-137,180-196`,
  `_windows_resolver.py:55-69,98-112,151-165`). Итоговый handle передаётся
  вызывающему.
- Пиковое приращение одного resolver относительно заимствованных
  `root/parent` — не более двух handles на обеих платформах: текущий owned
  prefix плюс открываемый probe/content/result. После ошибки приращение равно
  нулю; после успеха остаётся только result.
- В полном DFS POSIX консервативный пик — root, 33 активных directory handles,
  до 33 ресурсов `scandir` и до двух resolver handles, то есть порядка 69
  descriptors/resources. Windows-пик — root, 33 directory handles и до двух
  resolver handles, то есть не более 36 handles; 33 активных enumeration
  buffers занимают `33*65_536=2_162_688` байт.

## Complexity по всем путям

| Path | O(time) | O(memory/resources), дополнительная | Числовой bound | Итог |
|---|---:|---:|---|---|
| Root `Path.resolve(strict=True)` | `O(P+links)` + metadata I/O | `O(P)` | Cycle/ошибка становятся `root_unreadable`; прикладных retries нет | OK |
| POSIX root open/check | `O(P)` + fixed `open/fstat/final-path` calls | один root handle, `O(P)` | Root handle закрывает `open_registry` | OK |
| Windows root probe/open/check | `O(P)` + две fixed группы native calls | не более одного probe/content handle до публикации root | Probe и error path закрыты | OK |
| Root enumerate, normal | `O(K)` до sort | `O(n+K)` | `n<=256` | OK |
| Root enumerate, overflow | `O(K_257)` | `O(R+K_257)` | остановка на 257-м имени, один root issue | OK |
| Root deterministic sort | `O(K+n log n*M)` | `O(n+K)` | сортируется не более 256 имён | OK |
| Safe report identifier | `O(K)` total | `O(M)` transient | не более одного SHA-256 на root entry | OK |
| Candidate identity/attempt dedup | `O(R)` average | `O(S)` | set кратко `<=129`, file attempts `<=128` | OK |
| POSIX plain component | fixed `stat/open/fstat` + `O(P)` final path | до двух resolver handles | все opens относительно удерживаемого parent | OK |
| POSIX symlink component | fixed `stat/readlink` + `O(P+Y+C)` normalize/requeue | `O(P+Y+C)` transient | общий `steps<=256`, `hops<=40`; `Y` ограничен filesystem | OK |
| Windows plain component | fixed probe, content open, metadata/final path `O(P)` | owned prefix + один probe/content token | probe закрывается до content; no-follow relative open | OK |
| Windows reparse component | fixed probe/IOCTL + `O(Z+P+Y+C)` parse/requeue | `O(Z+P+Y+C)`, до двух handles | `Z<=16_384`, unsupported tags немедленно отклоняются | OK |
| Один полный resolver | `O(C*P+H*(C+P+Y+Z))` CPU, `O(C)` fixed syscall groups | `O(C+P+Y+Z)`, stack `O(1)`, handles `<=2` | `visits+hops<=256`, `hops<=40`, сумма длин принятых queues `<=10_240` | OK, PERF-004 |
| Все resolver calls load | `O(U*(C*P+H*(C+P+Y+Z)))` | один resolver active за раз | charged steps `<=U*C=33_652_736`; hops `<=5_258_240` | OK, bounded |
| Final containment | `O(P)` | `O(P)` transient | одна нормализация пары и `commonpath`, без обхода всех parents | OK |
| `SKILL.md` open/type/read guard | resolver + fixed checks | один file handle поверх root/directory | не более `S=128` attempts; non-regular не читается | OK, PERF-001 |
| `_read_up_to`, full/short | POSIX `O(r+c)`; Windows worst-case `O(L^2)` allocation work при one-byte reads | peak `O(L+c)` POSIX, `O(L)` Windows | `r,c<=L+1`; весь load `<=S*(L+1)=8_388_736` calls/bytes | OK, bounded |
| Per-file/aggregate overflow | `O(min(L,remaining)+1)` | `O(L)` | parser не вызывается | OK |
| UTF-8/split/YAML/validation | `O(b_j)` на каждый последовательный проход | `O(b_j)` | `sum(b_j)<=T`; alias/anchor/tag отвергаются до construction | OK |
| Auxiliary early success | `O(k*C)` component I/O, `k<=A` | `O(k+D)` identities/DFS + resolver transient | немедленный возврат на первом regular file | OK |
| Auxiliary full negative | `O(e_j*C)` component I/O | `O(A+D)` + platform iterator resources | `e_j<=1_024`, visited `<=1_025`, depth `<=32` | OK, PERF-002 |
| Auxiliary entry/depth overflow | work первых `A` entries либо `D` levels | `O(A+D)` | 1025-е имя не открывается; depth 33 закрывается при unwind | OK |
| Auxiliary cycle/shared directory | один bounded open + average `O(1)` set lookup | входит в visited `O(A)` | повторная identity не перечисляется | OK |
| POSIX DFS resources | входит в bounded traversal | `O(D)` handles/scandir, консервативно около 69 с resolver | все генераторы и handles закрываются при return/exception | OK |
| Windows DFS resources | входит в bounded traversal | handles `<=36`, buffers `<=2_162_688` bytes | один 64 KiB buffer на активный depth | OK |
| `Skill`/`LoadIssue` accumulation | `O(Q+I)` | `Theta(Q+I)` — обязательный output | skills `<=128`, issues/log events `<=256`, `Q=O(T)` | OK, PERF-003 |
| List → immutable tuples | `O(S+I)` | краткая копия `O(S+I)` refs | не более 384 output refs | OK |
| Полный load | `O(K+R log R+T+E+U*(C*P+H*(C+P+Y+Z)))` | `O(T+R+S+A+D*B+C+P+Y+Z)` | все счётчики, bytes, stack и handles имеют явный предел | OK |

POSIX absolute/relative symlink target нормализуется и сводится к пути
относительно уже открытого root до replay (`_posix_io.py:220-238`). Windows
reparse target ограничен нативным буфером, UNC/device target отклоняется, а
внутренняя цель сводится к root-relative компонентам до открытия
(`_windows_reparse.py:36-67,154-210`). Обычный Windows child открывается
непосредственно относительно переданного parent; replay от root происходит
только после reparse point. Таким образом, external target не добавляет
неограниченный traversal path.

Корневое перечисление по-прежнему останавливается на 257-м имени. Auxiliary
обход потоковый, останавливается после 1024 открываемых entries либо на depth
33, дедуплицирует directory identity и не сортирует дерево
(`_filesystem.py:68-82,232-339`). Итоговый отчёт удерживает не больше 128
скиллов и 256 локальных issues; root failure заменяет результат одним issue
(`_registry.py:42-66,84-88,147-157`).

Дополнительное исправление не требуется.

## Findings

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.
