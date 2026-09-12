# Выжимка

Роль: perf
Scope: `dd817f1..8a5eabf`
Вердикт: **GATE_OK**
Находки P0–P3: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Delta закрывает `E1-S4-R2-PERF-001`: `_new_marker()` делает не больше
`_FLASH_MARKER_ATTEMPTS = 8` вызовов `token_urlsafe(32)` и при исчерпании
поднимает `ReloadUnavailableError` до файлового scan. Цикл больше не держит
`_state_lock` неограниченно.

Flash хранит только `_PublicCatalog` (публичные `SkillSummary` и
`ReloadSummary`) с TTL 300 с. Очистка ленивая и проходит по `f <= 8` записям.
Admission по-прежнему допускает один `F` на экземпляр: `acquire_nowait()`,
затем резерв маркера, затем один `run_in_threadpool(self._registry.reload)`.
Поиск остаётся внутри `n,q <= 128`.

Benchmark и тесты не запускались согласно ограничению задачи. Вывод основан
на статической проверке committed delta, текущего кода и зафиксированного
AnyIO 4.10.0.

# Что закрыто относительно `e1-s4-rework-2-perf.md`

| Проверка | Было | Стало |
|---|---|---|
| Marker collision-loop | `while marker in flashes` без потолка; `K` не ограничен | `for _ in range(8)`; затем контролируемый отказ; `K <= 8` |
| TTL cleanup | слоты жили до точного GET или рестарта | `expires_at = now + 300`; `_purge_expired` на reserve/publish/consume; `O(f)`, `f <= 8` |
| Flash retention | `_CatalogGeneration.report` с телами `Skill.body` | `_FlashEntry.catalog: _PublicCatalog`; тел нет |
| Reload admission | один `F` через `acquire_nowait` | тот же один `F`; резерв маркера стоит до scan |
| Search | `O(q + n log n + Lq)`, `n,q <= 128` | публичный фильтр `O(q + L)` по уже построенному поколению; построение `O(n log n + L + P)`; пределы те же |

# Обозначения и пределы

- `r` — число корректных POST, которые достигают admission, пока один владелец
  удерживает `_reload_gate`.
- `F` — одна полная файловая загрузка и публикация registry snapshot; `tau` —
  её wall time.
- `n <= 128` — число принятых скиллов; `m <= n` — число результатов одного
  поиска.
- `i <= 129` — число `LoadIssue`; при обычном ограниченном обходе
  `n + i <= 129`.
- `B <= 1 048 576` — суммарные байты `SKILL.md`; один файл не больше
  65 536 байт. Description не длиннее 1 024 символов; name — 64.
- `A <= 128 * 1 024` — суммарное число просмотренных auxiliary entries;
  глубина одного дерева не больше 32.
- `q <= 128` — длина допустимого поискового запроса.
- `b <= 4 096` — накопленное тело reload-формы.
- `L` — сумма длин searchable-полей `name + caption` поколения.
  Caption лежит внутри файлового бюджета, поэтому `L = O(B + 64n)`.
- `P` — сумма длин публичных `name + caption + description`;
  `P = O(B + 64n + 1 024n)`.
- `J = O(128i)` — суммарный размер безопасных issue-сообщений: path не длиннее
  128 символов либо заменяется 16-hex hash-id, message/action берутся из
  конечного набора шаблонов.
- `M = O(B + P + n + i + loader overhead)` — один immutable `LoadReport`
  текущего registry-поколения вместе с телами. Flash это поколение не хранит.
- `f` — число непотреблённых flash entries, `0 <= f <= 8`.
- `c` — число уже занятых marker при генерации; после purge и preflight
  `0 <= c <= 7`.
- `K` — число попыток `token_urlsafe(32)` для одного marker; теперь
  `1 <= K <= 8`.
- `g` — число одновременно выполняющихся GET, удерживающих уже построенные
  публичные каталоги.
- `H` — суммарная длина просмотренных HTTP-заголовков; `p` и `a` — длина
  static path и размер asset.
- `T = 300` — TTL flash в секундах монотонных часов.

Унаследованные loader-budget не изменены: не больше 256 root entries,
128 попыток каталогов, 1 MiB `SKILL.md`, 1 024 auxiliary entries на skill,
32 уровней, 256 resolution steps и 40 link hops на `open_child`; Windows
reparse buffer равен 16 384 байтам. Консервативно
`F = O(root_sort + B + A * resolver_cost + n log n)`. Эти счётчики ограничивают
число операций и объём данных, но не wall time отдельного filesystem syscall.

# Marker loop

`src/skillhub/web/_catalog.py:195-200` больше не содержит `while`.

Один вызов `token_urlsafe(32)` читает фиксированные 32 байта и даёт 43
URL-safe символа. Резерв вызывается под `_state_lock` только после
`_purge_expired` и проверки `f < 8` и отсутствия `_reservation`:

- строго `K <= 8` сравнений с словарём из не более семи ключей;
- `in` по hash-dict даёт `O(K)` времени и `O(43)` дополнительной памяти;
- исчерпание восьми коллизий поднимает `ReloadUnavailableError` до
  `run_in_threadpool`;
- `_reservation` в этом случае не назначается;
- caller (`_reserve_marker` / `_perform_reload`) возвращает 303
  `notice=reload-unavailable` и в `finally` освобождает `_reload_gate`.

При исправном CSPRNG `E[K] ≈ 1`, а worst case теперь hard `O(1)`, а не
expected-only. Критерий закрытия `E1-S4-R2-PERF-001` выполнен.

# TTL cleanup

`_purge_expired` строит список ключей с `expires_at <= now` и удаляет их.

- вход: `f <= 8` записей;
- время: один проход плюс не больше восьми `del` — `O(f) = O(1)`;
- память: временный список ключей `O(f * 43) = O(1)`;
- вызов только под `_state_lock` в `reserve_reload`, `publish_reload`,
  `consume`;
- фонового sweeper, таймера или неограниченного heap нет;
- просроченные слоты не ждут клиентский GET: следующий reserve видит
  `f < 8` после purge и может допустить новый `F`;
- свежая запись с `expires_at > now` не вытесняется.

Пока никто не вызывает reserve/publish/consume, просроченные объекты могут
оставаться до следующего из этих путей. Это не утечка: `f` всё равно не
превышает 8, а каждый объект — публичная проекция, не `M`.

# Flash: публичная проекция

`_FlashEntry` содержит `_PublicCatalog` и `float expires_at`.

`_PublicCatalog.skills` — `tuple[SkillSummary, ...]`: `name`, `caption`,
`description`, `has_files`. Поля `Skill.body` нет. `ReloadSummary` хранит
`loaded`, `skipped` и `LoadIssueSummary(message, action)` без `path`/`reason`
модели и без тел файлов.

`publish_reload` строит проекцию до взятия lock: `report.search("")` даёт
ссылки на уже существующие `Skill` текущего `F`, `_public_skills` копирует
только публичные поля. В словарь попадает одна новая ссылка на `_PublicCatalog`,
не `LoadReport`.

Shared flash-память: `O(f(P + J))` при `f <= 8`. Полные тела остаются только
в одном текущем `RegistryCapture` реестра (`O(M)`) плюс transient новый report
во время `F`. Прежний пик «до восьми полных `LoadReport`» снят.

# Admission: один scan

Порядок `POST /skills/reload` в `src/skillhub/web/routes.py:95-120`:

1. Origin, Content-Type, тело и CSRF проверяются до admission.
2. `acquire_nowait()` либо делает запрос единственным владельцем per-app gate,
   либо сразу возвращает 303 `notice=reload-unavailable` без ожидания и без `F`.
3. Владелец вызывает `reserve_reload()`: purge, проверка `f` и `_reservation`,
   затем bounded CSPRNG. Отказ — тот же 303, `0 F`.
4. Только после резерва: один `run_in_threadpool(self._registry.reload)`.
5. `checkpoint_if_cancelled()`; успех публикует проекцию; иначе
   `discard_reservation` в `finally`.
6. `_reload_gate.release()` всегда в `finally` внешнего `reload_skills`.

Следствия для `r` пересекающихся корректных POST:

- не больше одного `F`;
- остальные `r - 1` дают по одной `O(1)` gate-проверке и фиксированному
  redirect;
- суммарная работа после валидации: `F + O(r)`, не `O(rF)`;
- нет waiter на AnyIO lock и нет отложенных повторных scan;
- одновременно файловый reload занимает не больше одного worker token;
- collision-exhaustion и полная ёмкость не запускают scan.

Отдельные процессы имеют собственный gate. Registry `_reload_lock` по-прежнему
сериализует только писателей; `capture()` / `snapshot` / `search` его не берут.

# Search

`_PublicCatalog.search`:

- `len(query) > 128` → исключение до сканирования;
- один `query.casefold()`;
- линейный проход уже упорядоченного `self.skills`;
- `_matches` смотрит только `name` и `caption`;
- выход — не больше `m <= n` ссылок на уже построенные `SkillSummary`.

Время `O(q + L)`, память `O(q + m)`. Сортировка на этом шаге отсутствует.

Построение публичного каталога (`public_catalog` / `publish_reload`) один раз
вызывает `search("")` реестра: сортировка `n` ссылок и проекция полей —
`O(n log n + L + P)` времени и `O(n + P + J)` памяти. Это тот же класс, что
и прежний `LoadReport.search`, и те же пределы `n,q`.

`GET /api/skills` и `GET /skills` без flash каждый раз заново проецируют
текущий `RegistryCapture`. Это не второй файловый scan и не рост shared store:
временные `SkillSummary` живут в request. Flash-hit повторно проекцию не строит.

# Отмена и locks

- Отмена во время валидации формы — до gate, reserve, pool и `F`.
- Busy-request не владеет gate и не вызывает CSPRNG/`F`.
- `run_in_threadpool` сохраняет `abandon_on_cancel=False`: уже начатый `F`
  завершается один раз.
- Отмена после `F` и до `publish_reload` не добавляет flash: reservation
  сбрасывается, `_state_lock` и gate освобождаются.
- `_purge_expired` / `dict.pop` / смена `_reservation` выполняются только
  внутри `with self._state_lock`.
- GET с валидным marker атомарно забирает слот; неизвестный/истёкший marker
  не меняет current generation и не запускает `F`.

# Сводка complexity

| Path / ветвь | n/m | O(time) | O(memory) | Bound |
|---|---|---|---|---|
| `create_app()` success | n<=128 | `F + O(J + n) + CSPRNG(32)` | `O(M + J + n)` | один sync startup `F`; flash пуст |
| `create_app()` root failure | n=0, i=1 | до первого root failure + `O(1)` | `O(1)` | пустой report, одна issue |
| `create_router()` / CSRF validate | — | `O(C)` | `O(C)` | `1 <= C <= 256` |
| `RegistryCapture.search` / `SkillRegistry.search` | n<=128, q | `O(q + n log n + Lq)` | `O(q + n + m)` | snapshot уже есть; HTTP этот путь не кормит сырым q без проекции |
| `_capture_from_report` | n<=128 | `O(n log n)` | `O(n)` | один mapping на поколение |
| `_CatalogState.reserve_reload` success | c<=7 | `O(f + K)` | `O(43)` | `f<=8`, `K<=8`; до `F` |
| `_CatalogState.reserve_reload` full / busy reservation | f=8 или reservation | `O(f)` | `O(1)` | `0 F`, `0` новых ключей |
| `_new_marker()` | c<=7 | `O(K)` | `O(43)` | **hard `K<=8`**; отказ → 303, не scan |
| `_purge_expired` | f<=8 | `O(f)` | `O(f)` | только ключи; нет фонового цикла |
| `_CatalogState.publish_reload` | n<=128, i<=129 | `O(n log n + L + P + J + f)` | transient `O(P + J)`; store `O(f(P+J))` | тела не кладутся в `_flashes` |
| `_CatalogState.discard_reservation` | — | `O(1)` | `O(1)` | одна строка или None |
| `_CatalogState.consume(None)` | f<=8 | `O(f)` | `O(1)` | только purge; current не читает flash |
| `consume(malformed)` | — | `O(f)` + fixed regex | `O(1)` | без `pop` полезной записи |
| `consume(valid hit)` | f<=8 | `O(f)` + `O(1)` pop | `O(1)` ссылка | слот освобождается |
| `consume(valid miss/expired)` | f<=8 | `O(f)` | `O(1)` | fallback к current; `0 F` |
| `public_catalog(capture)` | n<=128 | `O(n log n + L + P + J)` | `O(P + J + n)` | request-local; без тел в результате |
| `_PublicCatalog.search` valid | n<=128, m<=n, q<=128 | `O(q + L)` | `O(q + m)` | без sort; без body |
| `_PublicCatalog.search` `q>128` | — | `O(1)` после `len` | `O(1)` | до фильтра |
| `build_skills_page` valid | m<=n, i<=129 | search + `O(P_m + J)` | `O(P_m + J + n)` | один generation |
| `build_skills_page` `q>128` | i<=129 | `O(J)` | `O(J)` | query очищен |
| `GET /health` | — | `O(1)` | fixed JSON | без registry/flash |
| `GET /` | — | `O(1)` | fixed HTML | без registry/flash |
| `GET /api/skills?q` valid | n<=128, m<=n | `O(n log n + L + P + q)` | `O(P + m)` JSON | `m<=128`; body/issues нет |
| `GET /api/skills?q` over limit | — | framework decode + projection + `O(1)` | `O(P + J)` transient + fixed 422 | до фильтра; без `F` |
| `GET /skills?q` без marker | n, m, i | consume `O(f)` + projection + search + render | `O(P_m + J + n)` HTML | lock-free registry capture; flash не растёт |
| `GET /skills?reload=` hit | n, m, i | consume + `O(q + L + P_m + J)` | предыдущая строка | один из `f<=8`; `no-store` |
| `GET /skills` unknown/expired marker | n, m, i | consume + current projection | предыдущая строка | `0 F`; `no-store` |
| `GET /skills?notice=` | n, m, i | как без marker + `O(1)` флаг | + фиксированный banner | сравнение с константой; `no-store` |
| POST reload: bad Origin/Host/type | — | `O(H)` | `O(1)` / bounded parser | до gate/`F` |
| POST reload: `Content-Length` / body > 4096 | — | `O(header)` / до 4097 байт | `<= 4096` накопленных | `413` до gate/`F` |
| POST reload: bad form/CSRF | — | `O(b)` | `O(b)`, `b<=4096` | до gate/`F` |
| POST reload: busy gate | — | validation + `O(1)` | fixed 303 | `0` reload workers, `0 F` |
| POST reload: flash full / collision exhaust | f=8 / K=8 | validation + `O(f + K)` | fixed 303 | `0 F`; gate кратко взят |
| POST reload: success | n, i | validation + `F + O(J+n+K+P)` | active `M` + flash `O(P+J)` + 43 B | один claimant, один `F`, `K<=8` |
| Отмена admitted POST во время `F` | — | до конца одного `F` | одна active load | flash не добавляется |
| `r` пересекающихся POST, ёмкость есть | — | `O(sum(validation_j) + F + J + K + P + r)` | shared `O(M + 8(P+J))` + `O(r)` | один `F`; `r-1` constant reject |
| `r` пересекающихся POST, flash полон | — | `O(sum(validation_j) + r)` | `O(r)` | ноль `F` |
| 8 abandoned + TTL expiry + следующий POST | f=8→0 | `8F` затем `O(f)` purge + один `F` | `O(8(P+J))` затем слот свободен | девятый до TTL — 303; после TTL — один scan |
| Static GET | — | `O(p + a)` | `a <= 232 111` | один mounted directory |
| `/openapi.json` first/repeat | — | `O(routes + schema)` / serialize | bounded schema | пять прикладных routes |
| `app.js` transient query | — | `O(U)` | `O(U)` | browser-only; два ключа |
| `skills.html` unavailable banner | — | `O(1)` | `O(1)` | фиксированный markup |
| Изменённые `tests/*.py` | — | N/A | N/A | в этом gate не исполнялись |

# Finding

Находок P0–P3 нет.

# P0–P3

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.

**GATE_OK**
