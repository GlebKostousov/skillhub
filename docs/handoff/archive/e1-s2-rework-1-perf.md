# Выжимка
Роль: perf
Вердикт: GATE_OK
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

`PERF-E1-S2-001/002/003` закрыты в дельте `9353481..ca2fc09`.
Содержимое читается только из уже открытого обычного файла и не более заданного
числа байтов; FIFO, device, pipe и directory до чтения отвергаются. Рекурсивный
`has_files` теперь потоковый, без сортировки, с пределами entries/depth и
visited identity. Корень, число попыток скиллов, суммарный объём разбора и
размер отчёта имеют выводимые числовые границы.

# Отчёт

Проверены договор E1-S2 (`docs/phases/orchestrator-e1.md:107-151`), предыдущий
perf gate (`docs/handoff/e1-s2-perf.md`) и только дельта
`9353481..ca2fc09`. Анализ статический; код и тесты не запускались и не
изменялись.

Обозначения:

- `R=256` — `MAX_ROOT_ENTRIES`, `S=128` — `MAX_SKILLS`;
- `L=65_536` — `MAX_SKILL_FILE_BYTES`;
- `T=1_048_576` — `MAX_TOTAL_SKILL_BYTES`;
- `A=1_024` — `MAX_AUX_ENTRIES`, `D=32` — `MAX_AUX_DEPTH`;
- `n` — число имён, полученных из корня: на normal path `n <= R`, при
  переполнении читается не более `R+1`;
- `s <= S` — число уникальных directory identity, дошедших до открытия
  `SKILL.md`;
- `b_j` — принятые байты файла `j`, `sum(b_j) <= T`;
- `r_j` — реально прочитанные байты файла `j`,
  `r_j <= min(L, remaining)+1 <= L+1`;
- `e_j` — открытые auxiliary entries скилла `j`, `e_j <= A`; для обнаружения
  overflow iterator отдаёт не более `A+1` имён, но последнее уже не открывается;
- `v_j` — visited directory identities одного обхода,
  `v_j <= e_j+1 <= A+1`;
- `P/H` — длина конечного пути и число разрешаемых filesystem links;
- `K/M` — суммарная/максимальная длина не более `R` корневых имён;
- `Q` — объём строк, удерживаемых итоговыми `Skill`;
- `I` — число `LoadIssue`, `I <= R` для обработанного корня либо один
  root-level issue.

Оценки wall time предполагают, что системный вызов файловой системы
завершается. Код не задаёт deadline обычному локальному/сетевому filesystem
I/O, но после возврата вызовов не содержит неограниченного пользовательского
цикла, чтения до EOF или чтения специального объекта.

## Закрытие прежних findings

### PERF-E1-S2-001 — закрыт

`SKILL.md` открывается один раз, свойства и конечный путь берутся с этого
handle, containment и `is_regular` проверяются до `_read_up_to`, а байты
читаются с того же handle (`_filesystem.py:126-158`). Замена файла после
открытия не меняет читаемый объект.

На POSIX child открывается относительно `dir_fd` с
`O_RDONLY|O_NONBLOCK|O_CLOEXEC`; затем выполняются `fstat` и получение
final path. FIFO/device не проходит `S_ISREG`, а `O_NONBLOCK` не даёт открытию
FIFO ожидать writer (`_posix_io.py:40-70,87-122`). На Windows после
`CreateFileW` выполняются `GetFileInformationByHandle`,
`GetFinalPathNameByHandleW` и `GetFileType`; `ReadFile` вызывается только для
`FILE_TYPE_DISK`, не помеченного directory. Pipe/character handle содержательно
не читается (`_windows_io.py:164-219`). Сам read budget не превышает `L+1`, а
набор short reads прекращается после исчерпания этого budget.

### PERF-E1-S2-002 — закрыт

`has_additional_files` использует ленивые `os.scandir` на POSIX и
последовательные 64 KiB буферы `GetFileInformationByHandleEx` на Windows.
Имена auxiliary entries не материализуются и не сортируются. Каждый entry до
открытия расходует единый счётчик; `A+1` даёт
`directory_limit_exceeded`. Спуск в каталог допускается только до `D`, а
каждый фактически открытый каталог дедуплицируется по `(st_dev, st_ino)` либо
`(volume_serial_number, file_index)` (`_filesystem.py:106-123,160-226`).

Внутренние symlink/junction разрешаются в фактический handle, внешний final
path не перечисляется. Цикл на уже посещённую identity завершается без
повторного обхода. Максимальная Python-рекурсия составляет порядка `4D+O(1)`
кадров, а одновременно открытые handles — `O(D)`; при `D=32` это далеко от
обычного recursion limit и не зависит от физического размера или цикличности
дерева.

### PERF-E1-S2-003 — закрыт

Корень прекращает перечисление на имени `R+1` и возвращает один fail-closed
issue до parsing; сортируется только список из не более `R` имён
(`_filesystem.py:64-81,224-226`). После `S` уникальных каталогов следующий
каталог останавливает загрузку. Перед каждым decode/parse read size сужается
оставшимся aggregate budget, а `remaining_bytes` уменьшается до вызова parser
(`_registry.py:159-191`, `_filesystem.py:139-146`).

Поэтому decode, split и оба YAML-прохода в сумме получают не более `T` байт.
Oversized-файл не списывает `remaining_bytes`, но всё равно расходует одну из
`S` попыток; даже в этом пути общий сырой read ограничен
`S*(L+1)=8_388_736` байт, а parser не вызывается. YAML разбирается строго
последовательно по одному файлу: список payload или несколько одновременно
живых parse trees не создаются.

## Complexity по всем путям

| Path | Вход / системные вызовы | O(time) | O(memory), дополнительная | Числовой bound | Итог |
|---|---|---:|---:|---|---|
| Root `Path.resolve` | исходный path, `P/H` | `O(P+H)` + metadata I/O | `O(P+H)` | Link cycle переводится в `root_unreadable`; path/hops ограничивает ОС | OK |
| POSIX root open/classify | `open`, `fstat`, `/proc/self/fd` или `/dev/fd` `readlink`; macOS `F_GETPATH` | `O(P+H)` + 3–4 calls | `O(P)` | Один handle; non-directory не перечисляется | OK |
| Win32 root open/classify | `CreateFileW`, `GetFileInformationByHandle`, 2×`GetFinalPathNameByHandleW`, `GetFileType` | `O(P+H)` + 5 calls | `O(P)` | Один handle и один final-path buffer | OK |
| Root enumerate, normal | `n <= R`; POSIX `scandir`, Win32 64 KiB batches | `O(K)` до sort | `O(n+K)` | Не более 256 имён удерживается | OK |
| Root enumerate, overflow | минимум `R+1` существующих имён | `O(K_257)` | `O(R+K_257)` | Остановка на 257-м имени, один issue, parsing отсутствует | OK |
| Bounded root sort | `n <= R`, max name `M` | `O(K+n log n*M)` worst-case compare | `O(n+K)` | Сортируется не более 256 keys; auxiliary tree не сортируется | OK |
| Root entry open/classify | до `n` entries, handle metadata/final path | `O(n(P+H))` + `O(n)` fixed call groups | `O(P)` transient, `O(S)` identities | Не более 256 opens; non-directory сразу закрывается | OK |
| Directory dedup / skill-attempt limit | unique identity count `s` | `O(s)` average set work | `O(S)` | Не более 129 identities кратко; не более 128 file attempts | OK |
| `SKILL.md` handle open/guard | POSIX anchored `openat` semantics либо Win32 open + actual-handle metadata | `O(P+H)` + fixed calls на attempt | `O(P)` | Не более `S` attempts; non-regular/escaped handle не читается | OK, PERF-001 |
| `_read_up_to`, normal/short reads | `r_j <= L+1` | `O(r_j+c_j)` calls/copy | `O(r_j+c_j)` objects до `join` | Даже при one-byte short reads `c_j <= L+1`; один payload `<=65_537` | OK |
| Per-file/aggregate overflow read | oversized или `remaining < file` | `O(min(L,remaining)+1)` | То же | Parser не вызывается; весь load читает не более `S*(L+1)` | OK |
| UTF-8 + newline normalization | принятый `b_j` | `O(b_j)` | `O(b_j)` с постоянным множителем строковых копий | Один файл `<=L`, сумма входа `<=T` | OK |
| Delimiters/body split | строки/lines одного файла | `O(b_j)` | `O(b_j)` | `splitlines`, два bounded `join`, `strip`; между файлами не накапливается | OK |
| YAML graph scan + `safe_load` | front matter `y_j <= b_j` | `O(y_j)` на каждый из двух последовательных проходов | `O(y_j)` tokens/constructed graph | Alias/anchor/tag отвергаются до construction; recursion/parser errors изолируются; `sum(y_j) <= T` | OK |
| Metadata validation | scalar text внутри `b_j` | `O(b_j)` worst-case | `O(1)` сверх YAML graph | `name<=64`, `description<=1024`; caption/body уже внутри `L` | OK |
| Duplicate canonical name | до `S` names | `O(S)` average total | `O(S)` | Не более 128 имён; duplicate не запускает auxiliary walk | OK |
| Auxiliary early `True` | файл на позиции `k <= A` | `O(k(P+H))` + enumeration/open calls | `O(v_k+D*P)` | Немедленный возврат на первом internal regular file | OK |
| Auxiliary полный отрицательный | `e_j <= A`, depth `<=D` | `O(e_j(P+H))` average | `O(v_j+D*P)` | Не более 1024 opened entries и 1025 identities с корнем | OK, PERF-002 |
| Auxiliary entry/depth overflow | имя `A+1` или каталог на depth `D+1` | `O(A(P+H))` / `O(D(P+H))` | `O(A+D*P)` | Стабильный `directory_limit_exceeded`; частичный `Skill` не публикуется | OK |
| Auxiliary cycle/shared target | повторная directory identity | `O(1)` average lookup после open | `O(v_j)` | Повтор не перечисляется; work всё равно входит в `A` | OK |
| POSIX auxiliary resources | DFS до `D` | входит в строки выше | `O(A+D*P)` + `O(D)` scandir resources | Порядка `2D+O(1)` descriptors в консервативной оценке, все закрываются context manager | OK |
| Win32 auxiliary resources | DFS до `D`, buffer 65_536 на активный iterator | входит в строки выше | `O(A+D*P+D*65_536)` | Не более `D+1=33` enumeration buffers (≈2.1 MiB) и `D+3=35` handles | OK |
| `Skill`/`LoadIssue` accumulation | `Q`, `I` | `O(Q+I)` | `Theta(Q+I)` — обязательный output | `Q=O(T+64S)`, skills `<=128`, issues/log events `<=256` | OK, PERF-003 |
| List → immutable tuples | skills `<=S`, issues `<=I` | `O(S+I)` | `O(S+I)` краткая копия ссылок | Не более 384 ссылок перед освобождением state lists | OK |
| Полный стабильный load | все пути | `O(K+n log n*M+(n+S+sum e_j)(P+H)+sum r_j+T)` | `O(T+R+S+A+D*P)`; на Win32 плюс `O(D*65_536)` | `n<=256`, `s<=128`, `sum r_j<=8_388_736`, `sum e_j<=128*1_024`, parser input/output `O(T)` | OK |

Сохранившийся `O(n log n)` относится только к обязательному детерминированному
порядку bounded-корня (`n<=256`). Плохой asymptotic на произвольной ширине
дерева устранён: булев auxiliary path больше не выполняет full sort. Других
неограниченных алгоритмических путей или накопления handles/parse trees/model
output в проверенной дельте нет; дополнительное исправление не требуется.

## Findings

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.
