# Выжимка
Роль: perf
Вердикт: GATE_BLOCKED
Рецепт: да
Находки: 3 — P0: 0, P1: 2, P2: 1, P3: 0.

Лимит `MAX_SKILL_FILE_BYTES=65_536` корректно ограничивает одно чтение,
UTF-8-декодирование и оба прохода PyYAML. Alias/anchor/tag отбрасываются до
`safe_load`, поэтому alias expansion отсутствует. Но загрузка в целом не имеет
предела по числу корневых записей, суммарным байтам, числу вспомогательных
файлов и глубине. Кроме того, проверка типа `SKILL.md` отделена от открытия,
а `has_files` может повторно обходить циклическое/подменённое дерево и сортирует
полные списки имён ради одного булева результата.

# Отчёт

Проверена только дельта `dd6d135..20748bc` и только поверхность
complexity/resources E1-S2: сканирование каталога, resolve/containment,
чтение/декодирование/YAML, рекурсивный `has_files`, сортировка, модели и рост
`LoadReport`. Анализ статический; код и тесты не запускались и не изменялись.

Обозначения:

- `n` — число непосредственных записей корня;
- `m <= n` — число уникальных resolved-каталогов, дошедших до `_load_skill`;
- `v <= m` — число валидных скиллов, `i <= n` — число `LoadIssue`;
- `L=65_536` — `MAX_SKILL_FILE_BYTES`;
- `b_j <= L+1` — реально прочитанные байты `SKILL.md` для каталога `j`;
  для принятого файла `b_j <= L`;
- `y_j <= b_j` — размер front matter, `B=sum(b_j)`;
- `K` — сумма длин имён корневых записей, `ell` — максимальная длина имени;
- `F_j` — число записей, перечисленных `has_files` до ответа для скилла `j`;
  `F=sum(F_j)`;
- `C_j <= F_j` — число имён файлов, для которых выполнен `resolve`;
- `D_j` — максимальная относительная глубина посещённого дерева;
- `W_j` — максимальная ширина одного посещённого каталога;
- `Q` — размер строк, удерживаемых итоговыми `Skill`; `Q=O(vL)`;
- `R` — число компонентов/переходов symlink при корневом и entry resolve.

Оценки CPU ниже предполагают неизменяемую ациклическую файловую систему и
завершающиеся системные вызовы. Время внешнего I/O само по себе кодом не
ограничено.

## Сводка complexity

| Path | Вход | O(time) | O(memory) | Фактический bound | Итог |
|---|---|---:|---:|---|---|
| `SkillRegistry.load`: root `resolve` + `is_dir` | глубина/ссылки `R` | `O(R)` + I/O | `O(R)` на resolved path | При цикле `Path.resolve` даёт `RuntimeError`; прикладного лимита hops/latency нет | Частично bounded |
| `root.iterdir()` + `sorted(...casefold...)` | `n`, `K`, `ell` | `O(K + n log n * ell)` worst-case сравнений | `O(n + K)` во время decorate/sort | Сортировка нужна для детерминированного отчёта, но `n` не ограничено | Unbounded по `n` |
| Entry `resolve` / containment / dedup | `n`, `R` | `O(nR)` + несколько metadata I/O на entry | `O(mR)` для `visited_directories` и resolved paths | Symlink aliases одного resolved path дедуплицируются; `root in path.parents` линейно по глубине | Unbounded по `n/R` |
| `_read_bounded` | фактический размер файла `s` | `O(min(s,L+1))` после открытия | `O(min(s,L+1))` | Один `read` не более `65_537` байт; oversized не декодируется | Bounded после успешного open |
| UTF-8 decode + два `replace` | `b_j <= L` | `O(b_j)` | `O(b_j)`; несколько одновременных строковых копий | Не более одного принятого файла за раз сверх накопленного отчёта | Bounded |
| `splitlines`, delimiter scan, два `join`, `strip` | `b_j <= L`, число строк `r_j` | `O(b_j)` | `O(b_j+r_j)` объектов/ссылок | Front matter и body вместе ограничены `L`; отдельные копии дают только постоянный множитель | Bounded |
| `yaml.scan` + `yaml.safe_load` | `y_j <= L` | `O(y_j)` + второй `O(y_j)` проход | `O(y_j)` parser/constructed graph | Alias, anchor и tag token отвергаются до construction; глубина ограничена байтами и Python recursion, `RecursionError` перехвачен | Bounded на файл |
| Metadata validation | scalar lengths внутри `b_j` | `O(b_j)` worst-case | `O(1)` сверх parser data | `name <=64`, принятый `description <=1024`; длинный description сканируется до проверки, но весь файл `<=L`; caption также внутри `L` | Bounded на файл |
| `_has_additional_files` на стабильном ациклическом дереве | `F_j,C_j,D_j,W_j` | `O(F_j(D_j + log W_j))` с path build/resolve и сортировкой | `O(F_j)` safe upper bound; peak stack/list зависит от `W_j,D_j` | Байты файлов не читаются, но entry/depth/width не ограничены; deep chain даёт квадратичную сумму длин путей, wide dir — сортировку `W_j log W_j` | Finding |
| `_has_additional_files` при цикле/подмене | число повторных посещений | Нет bound через число уникальных записей | Растёт со stack/path names | `followlinks=False` закрывает стабильные обычные symlink dirs, но нет identity set, лимита и защиты от junction/mount/race | Finding |
| `Skill`, `LoadIssue`, tuple materialization | `v,i,Q` | `O(v+i)` сверх уже созданных строк | `Theta(Q+v+i)`; кратко сосуществуют list и tuple ссылок | `slots/frozen` убирают лишние dict, но `Q <= vL`, а `v` не ограничено | Output-sized, aggregate unbounded |
| Полный `load`, стабильное дерево | все параметры | `O(K+n log n+nR+B+F(D+log W))` | `O(n+Q+L+max(walk_peak))` | Единственный жёсткий прикладной предел — `L` на один файл | Finding |

## Обязательные ресурсные сценарии

- Повторные сканы: каждый `load()` заново сканирует корень и каждое валидное
  auxiliary tree. Внутри одного скилла `SKILL.md` проходит отдельные
  `resolve`, `is_file`, `open`; каждый auxiliary candidate — `resolve` и
  `is_file`. Front matter намеренно токенизируется дважды: защитный `scan`,
  затем parser внутри `safe_load`. Этот двойной YAML-проход остаётся `O(L)` и
  сам по себе допустим.
- TOCTOU I/O: `resolve/is_file` и последующий `open` используют имя пути, а не
  один открытый дескриптор. Аналогично recursive walk проверяет и затем
  использует меняемые pathname. Per-file byte limit не ограничивает ожидание
  внутри `open`.
- Symlink cycles: root и `SKILL.md` проходят `Path.resolve`, поэтому обычный
  цикл там завершается контролируемым отказом. Стабильные directory symlink в
  `os.walk(...followlinks=False)` не обходятся. Однако walk не хранит identity
  каталогов; Windows junction/reparse, mount/bind cycle и race между проверкой
  и следующим `scandir` остаются повторно обходимыми.
- Deep tree: даже без цикла построение всё более длинных путей и `resolve`
  каждого candidate даёт `O(FD)`; для цепочки каталогов сумма длин путей
  достигает `O(D^2)`.
- Wide tree: `os.walk` сначала материализует все `directory_names/file_names`,
  после чего код сортирует оба списка. Для булева `has_files` это добавляет
  `O(W log W)` и задерживает early return.
- Parser alias expansion: отсутствует — `AliasToken`, `AnchorToken` и
  `TagToken` отвергаются до `safe_load`. Constructed graph имеет размер,
  линейно ограниченный текстом front matter; pathological nesting упирается в
  `L`/recursion и превращается в `invalid_yaml`.
- Рост результата: `LoadReport` обязан удерживать тела принятых скиллов, так
  что `Theta(Q)` является output lower bound. Но `Q <= vL` не превращается в
  численный bound без предела `v`; issues и warning-события также растут как
  `O(n)`.

## Findings

### P1 — PERF-E1-S2-001 — Check-then-open допускает бессрочно блокирующее чтение

Файл: `src/skillhub/registry/_registry.py:126-146`.

`_resolve_skill_file` сначала делает `resolve(strict=True)` и отдельный
`is_file()`, возвращает pathname, а `_read_bounded` позже заново открывает это
имя. Между проверкой и `open` запись можно заменить. Замена regular file на
FIFO/device означает, что выполнение может зависнуть ещё в `open`; чтение
`L+1` байт и его memory bound при этом не начинают действовать. Замена
компонента пути также разрывает доказательство containment для фактически
открытого объекта.

Конкретное исправление: объединить resolve/type-check/open/read в операцию над
одним дескриптором. Открывать `SKILL.md` относительно уже открытого каталога
(`dir_fd`/платформенный beneath-root handle) с no-follow и non-blocking
флагами, затем проверять `fstat`/final-handle path на regular file и
containment и читать с того же дескриптора ровно `L+1` байт. Ошибку
неподдерживаемого типа или reparse оформлять существующей стабильной причиной.

### P1 — PERF-E1-S2-002 — `has_files` не имеет конечного work bound

Файл: `src/skillhub/registry/_registry.py:212-235`.

Точный отрицательный ответ требует полного обхода всех потомков. У обхода нет
лимита entries/depth и множества identity каталогов. `followlinks=False`
исключает только обычный стабильный directory symlink; junction/reparse,
mount/bind cycle либо pathname, подменённый после проверки, может вернуть walk
к уже посещённому каталогу. Тогда число операций не ограничено числом
уникальных записей дерева. Даже в ациклическом случае каждый широкий каталог
полностью материализуется и сортируется, а каждый candidate заново проходит
полный `resolve`, что даёт `O(F(D+log W))` вместо линейного `O(F)`.

Конкретное исправление: заменить `os.walk` на streaming DFS через
`os.scandir`; порядок не сортировать, поскольку результат — только `bool`.
Не спускаться в directory symlink/junction/reparse, хранить identity реально
открытых каталогов (`st_dev, st_ino` либо платформенный file ID), а также
жёстко ограничить число просмотренных entries и глубину. Проверять regular
file по anchored descriptor/`DirEntry` без повторного full-path `resolve`.
Превышение бюджета должно давать отдельную стабильную причину
`directory_limit_exceeded`, а не частичный `has_files=False`.

### P2 — PERF-E1-S2-003 — Per-file limit не ограничивает весь `LoadReport`

Файлы: `src/skillhub/registry/_registry.py:71-102`,
`src/skillhub/registry/_models.py:6-48`.

`sorted(root.iterdir())` материализует все `n` записей до первой валидации.
Затем каждый из `v` валидных файлов может оставить в `Skill` почти `L` байт
текста. Поэтому memory результата — `Theta(vL+i)`, CPU — как минимум
`Omega(n+B)`, warning output — `O(i)`, а численного aggregate bound нет.
Миллион нерелевантных root entries расходует память/CPU сортировки без роста
полезного результата; множество валидных файлов способно исчерпать память,
несмотря на корректный лимит каждого файла.

Конкретное исправление: читать корень через bounded `islice` максимум
`MAX_ROOT_ENTRIES+1`; при overflow возвращать один root-level
`registry_limit_exceeded`, не выбирая недетерминированное подмножество, и
сортировать только bounded список. Дополнительно ограничить `MAX_SKILLS` и
`MAX_TOTAL_SKILL_BYTES`, увеличивая byte budget до decode/parse. Тогда
максимальный `LoadReport` и суммарный parser work выводятся непосредственно из
этих констант.

## Рецепт

1. Ввести descriptor-anchored bounded reader: open once, `fstat`, final
   containment/type check, `read(L+1)` с того же объекта.
2. Переписать `has_files` на несортируемый streaming DFS с запретом
   directory reparse, visited identity и бюджетами entries/depth.
3. Ввести aggregate limits корня, числа скиллов и суммарных bytes; overflow
   завершать одним детерминированным root-level issue.
4. Целевая сложность после исправления:
   `O(N log N + TOTAL_SKILL_BYTES + AUX_ENTRIES)` time и
   `O(N + TOTAL_SKILL_BYTES + AUX_DEPTH + VISITED_DIRS)` memory, где каждый
   символ — явная числовая константа конфигурации, а не размер произвольного
   дерева.

Итого: P0 — 0, P1 — 2, P2 — 1, P3 — 0.
