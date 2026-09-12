# Выжимка

Роль: perf
Scope: `6da2a10..0da3ae5`
Вердикт: **НУЖНА_ДОРАБОТКА**
Находки P0–P3: 1 — P0: 0, P1: 0, P2: 0, P3: 1.

Delta закрывает прежнюю load-amplification: для одного экземпляра приложения
пересекающиеся корректные POST больше не образуют очередь из `r` полных
загрузок. Неблокирующий `acquire_nowait()` допускает один `F`, а остальные
конкурирующие запросы получают малый `409` до dispatch файловой работы.
Проверка flash-capacity также стоит до `F`; восемь непотреблённых результатов
дают backpressure, а успешный `pop()` освобождает слот.

Строгий performance gate всё же не пройден: `_new_marker()` повторяет
`token_urlsafe(32)` в цикле без предела попыток. При исправном CSPRNG это
практически однопроходный алгоритм, но детерминированной верхней границы числа
CSPRNG-вызовов и времени нет. Поскольку цикл выполняется под `_state_lock`,
его неограниченный worst case удерживает также путь потребления flash.

Benchmark и тесты не запускались согласно ограничению задачи. Вывод основан на
статической проверке committed delta, текущего кода и зафиксированных
AnyIO 4.10.0 / Starlette 1.6.0.

# Обозначения и пределы

- `r` — число корректных POST, которые достигают admission, пока один владелец
  удерживает `_reload_gate`.
- `F` — одна полная файловая загрузка и публикация registry snapshot; `tau` —
  её wall time.
- `n <= 128` — число принятых скиллов; `i <= 129` — число `LoadIssue`;
  при обычном ограниченном обходе `n + i <= 129`.
- `B <= 1 048 576` — суммарные байты `SKILL.md`; один файл не больше
  65 536 байт.
- `A <= 128 * 1 024` — суммарное число просмотренных auxiliary entries;
  глубина одного дерева не больше 32.
- `q <= 128` — длина допустимого поискового запроса.
- `b <= 4 096` — накопленное тело reload-формы.
- `L` — сумма длин searchable-полей `name + caption` поколения;
  `P` — сумма длин публичных `name + caption + description`.
  Caption и description находятся внутри файлового бюджета, name ограничен
  64 символами, поэтому `L,P = O(B + 64n)`.
- `J = O(128i)` — суммарный размер безопасных issue-полей и сообщений:
  path не длиннее 128 символов либо заменяется фиксированным hash-id, а
  reason/message/action выбираются из конечного набора шаблонов.
- `M = O(B + P + n + i + loader overhead)` — полный immutable `LoadReport`
  одного поколения вместе с телами скиллов.
- `f` — число непотреблённых flash entries, `0 <= f <= 8`.
- `c` — число уже занятых marker при генерации; благодаря preflight-capacity
  `0 <= c <= 7`.
- `K` — число попыток `token_urlsafe(32)` для одного marker.
- `g` — число одновременно выполняющихся GET, удерживающих захваченные или
  уже извлечённые поколения.
- `H` — суммарная длина просмотренных HTTP-заголовков; `p` и `a` — длина
  static path и размер asset.

Унаследованные loader-budget не изменены: не больше 256 root entries,
128 попыток каталогов, 1 MiB `SKILL.md`, 1 024 auxiliary entries на skill,
32 уровней, 256 resolution steps и 40 link hops на `open_child`; Windows
reparse buffer равен 16 384 байтам. Консервативно
`F = O(root_sort + B + A * resolver_cost + n log n)`. Эти счётчики ограничивают
число операций и объём данных, но не wall time отдельного filesystem syscall.

# Single-flight и общий worker pool

Порядок `POST /skills/reload` в `src/skillhub/web/routes.py:83-100`:

1. Origin, Content-Type, тело и CSRF проверяются до admission.
2. `acquire_nowait()` либо немедленно делает запрос единственным владельцем
   per-app gate, либо поднимает `WouldBlock`.
3. Busy-ветка формирует фиксированный `409 reload_unavailable`; она не ожидает
   gate и не вызывает файловый `run_in_threadpool(self._catalog.reload)`.
   Зарегистрированный expected-error handler синхронный, поэтому Starlette
   кратко исполняет только построение/logging фиксированного отказа через
   общий pool.
4. Только владелец gate проверяет flash-capacity и может передать
   `_CatalogState.reload()` в общий pool.
5. `finally` освобождает gate на success, ожидаемой ошибке, исключении и
   cancellation.

Следовательно, если все `r` запросов действительно пересекаются на admission
с временем владения первого:

- выполняется не больше одного `F`;
- остальные `r - 1` дают по одной `O(1)` gate-проверке и фиксированному отказу;
- суммарная работа после уже выполненной валидации равна
  `F + O(r)` вместо `O(rF)`;
- полный request cost равен `O(sum(validation_j) + F + r)`, где каждое тело
  ограничено 4 096 байтами;
- нет `r - 1` waiter в AnyIO lock и нет отложенных повторных scan;
- одновременно файловый reload занимает не больше одного worker token;
- ожидающий свободный token request ещё не владеет token; каждый отказ после
  получения token занимает его только на constant error-handler и сразу
  освобождает.

Если admitted coroutine ждёт свободный token лимитера, token ещё не занят.
При этом gate уже закрыт, поэтому существует не больше одного reload-waiter
на сам pool, а новые POST не становятся reload-waiter. При штатных 40 AnyIO
tokens активный `F` сам занимает один; оставшиеся 39 делят обычные sync-path и
короткие sync error-handler конкурентных отказов. Burst может поставить
`O(r)` таких constant handlers в очередь лимитера, но ожидающие token задачи
token не удерживают и ни одна из них не ждёт `F` под синхронным reload-lock.
Это гарантия на один экземпляр `_Routes`; отдельные процессы или отдельно
собранные приложения имеют собственный gate.

Committed-сценарии
`test_overlapping_reload_is_rejected_and_next_reload_is_admitted` и
`test_busy_reload_does_not_wait_or_consume_worker_token` структурно проверяют
ровно один loader call, отсутствие второго scan и доступность отдельного
sync-probe. Второй сценарий повышает pool с одного token до двух перед
завершением `409`, поэтому его имя не доказывает ноль worker-dispatch для
синхронного exception handler. В этом perf-gate сценарии не исполнялись.

# Flash-capacity, публикация и память

`has_reload_capacity()` вызывается владельцем gate до
`run_in_threadpool(self._catalog.reload)`:

- при `f == 8` запрос получает `409` до `F`, CSPRNG и новой публикации;
- при `f <= 7` другой POST не может увеличить словарь, потому что gate уже
  занят; конкурентный GET способен только уменьшить `f`;
- `remember_reload()` после успешной загрузки доводит `f` максимум до 8;
- известный marker атомарно удаляется через `dict.pop()` под `_state_lock`,
  поэтому освобождённый слот виден следующей capacity-проверке;
- неизвестный или уже потреблённый marker не меняет current generation.

Marker имеет точную длину 43 URL-safe ASCII-символа. Regex до словаря принимает
только эту фиксированную форму. Словарь содержит максимум восемь ключей, поэтому
даже консервативный lookup с учётом всех entries остаётся `O(8 * 43) = O(1)`;
успешный marker показывается не больше одному конкурентному GET.

Shared steady-state удерживает максимум восемь различных flash/current
поколений. Перед публикацией нового report возможен transient peak до девяти
поколений: capacity допускает не больше семи flash, отдельно может жить current
после отменённой публикации, и строится один новый report. Registry snapshot
ссылается на те же `Skill`, добавляя bounded mapping. GET, уже захвативший
поколение или извлёкший его через `pop`, удерживает его до завершения ответа;
агрегатная request-owned память поэтому равна `O(gM)`, но shared store не растёт.

Непотреблённый redirect не имеет TTL и продолжает занимать один из восьми
слотов. Это осознанный bounded backpressure: память не течёт и новый scan при
полном store не начинается, но дальнейший admission возобновляется только
после GET точного marker либо рестарта процесса.

# CSPRNG marker

Один вызов `token_urlsafe(32)` читает фиксированные 32 байта энтропии и создаёт
фиксированные 43 символа. При идеальном независимом CSPRNG и `c <= 7`:

- вероятность коллизии одной попытки не больше `7 / 2^256`;
- `E[K] = 1 / (1 - c / 2^256)`, практически ровно 1;
- `Pr[K > k] <= (7 / 2^256)^k`;
- дополнительная память цикла `O(43)`, потому что локальная строка
  перезаписывается.

Это доказывает expected `O(1)`, фиксированный размер результата и
вероятностный tail, но не hard bound. Код
`src/skillhub/web/_catalog.py:169-173` использует `while marker in flashes`
без счётчика. Последовательность повторяющихся CSPRNG-значений оставляет
`K` неограниченным, а цикл выполняется внутри `_state_lock` в
`_CatalogState.reload()`.

# Отмена, исключения и ресурсы

- Отмена во время чтения/валидации формы происходит до gate, pool и `F`.
- Busy-request не ждёт и не владеет ресурсами reload после фиксированного
  отказа.
- После admission `run_in_threadpool()` использует штатную семантику
  AnyIO `abandon_on_cancel=False`: уже запущенная синхронная загрузка не
  бросается в фоне и завершает не больше одного `F`.
- Явный `checkpoint_if_cancelled()` стоит после возврата worker и до
  `remember_reload()`. Отмена во время `F` поэтому не создаёт недоступный
  flash-marker; новый current уже может быть атомарно опубликован, но flash
  capacity не расходуется.
- `_reload_gate.release()` находится в `finally`; registry `_reload_lock` и
  `_state_lock` находятся в `with`. Исключения loader, summary или CSPRNG
  освобождают все захваченные locks.
- Файловая граница закрывает directory/file handles своими context manager.
  Cancellation не может прервать отдельный sync filesystem syscall, но после
  него освобождаются token и locks.
- Если ответ перестал быть доставляемым уже после `remember_reload()`, entry
  остаётся до точного GET; его стоимость bounded одним из восьми слотов.
- Единственный путь без hard time bound помимо внешнего filesystem syscall —
  collision-loop marker; он вынесен в finding.

# Current, поиск и output bounds

`_CatalogState.current` читает один указатель без lock: `O(1)` времени и памяти.
Поколение frozen и связывает один report, summary и marker, поэтому API и HTML
не смешивают данные разных публикаций.

Изменённая `_search_skills()` теперь сортирует любой вход по `Skill.name`.
Один поиск:

- создаёт один `query.casefold()`;
- сортирует максимум 128 ссылок: `O(n log n)` времени и `O(n)` памяти;
- casefold/search проходит `name + caption`; консервативно
  `O(q + n log n + Lq)` времени;
- создаёт максимум `m <= n` публичных моделей.

При `q > 128` исключение возникает до сортировки и сканирования. API отдаёт
фиксированный `422`; HTML очищает отражаемый query и строит только bounded
issue-состояние. Публичный output не содержит `Skill.body`:

- `GET /api/skills` — максимум 128 объектов и `O(P + n)` JSON;
- `GET /skills` — максимум 128 карточек плюс 129 issue, то есть
  `O(P + J + n)` HTML;
- Jinja autoescape увеличивает wire size лишь постоянным коэффициентом;
- fixed error/redirect responses имеют `O(1)` payload;
- максимальный локальный static asset — Bootstrap размером 232 111 байт,
  сумма четырёх assets — 234 040 байт.

# Complexity по всем изменённым и публичным путям

| Path / ветвь | O(time) | Дополнительная O(memory/output) | Конкурентность / предел |
|---|---:|---:|---|
| `create_app()` startup success (`app_factory.py`) | `F + O(J + n) + CSPRNG(32)` | `O(M + J + n)` | Один sync startup reload; один fixed CSRF token; registry больше не экспортируется через `app.state` |
| `create_app()` missing/unreadable root | до первого root failure + `O(1)` summary | `O(1)` | Пустой report, одна issue |
| `create_router()` / `validate_csrf_token()` | `O(C)` | `O(C)` encoded token + фиксированные routes | Для валидной конфигурации `1 <= C <= 256`; до HTTP |
| Public facade `skillhub.web.StrictHostMiddleware` (`web/__init__.py`) | import/attribute `O(1)` | `O(1)` | Только исправление composition seam |
| `LoadReport.loaded`, `LoadReport.skipped` | `O(1)` | `O(1)` | Длины immutable tuple |
| `LoadReport.search(q)` (`registry/_models.py`) | `O(q + n log n + Lq)` | `O(q + n + max_field + m)` | `n,q <= 128`; canonical order |
| `SkillRegistry.search(q)` через изменённый helper | `O(q + n log n + Lq)` | `O(q + n + max_field + m)` | Snapshot уже упорядочен, но helper сортирует повторно; всё bounded |
| `_CatalogState.current` | `O(1)` | `O(1)` | Lock-free capture immutable generation |
| `_CatalogState.has_reload_capacity()` | `O(1)` | `O(1)` | Короткий `_state_lock`, `f <= 8` |
| `_CatalogState.reload()` без marker-collision | `F + O(J + n) + CSPRNG(32)` | transient до `O(9M + 9J + n)` | Выполняется в worker; публикует один current |
| `_new_marker()` expected | expected `O(1)` | `O(43)` | `c <= 7`, 256-bit draw |
| `_new_marker()` strict worst case | **не ограничено** | `O(43)` | Неограниченный collision-loop под `_state_lock`; `E1-S4-R2-PERF-001` |
| `_CatalogState.remember_reload()` | expected `O(1)` | одна ссылка/key `O(43)` | После checkpoint; `f` становится не больше 8 |
| `for_marker(None)` | `O(1)` | `O(1)` | Без lock, current |
| `for_marker(malformed)` | fixed-width regex `O(1)` после framework decode | `O(1)` handler | Без lock и dict; raw query уже принадлежит transport/framework |
| `for_marker(valid hit/miss)` | expected `O(1)`, strict `O(8 * 43)` | `O(1)` | Atomic `pop`; hit освобождает слот |
| `build_skills_page()`, valid query | `O(q + n log n + Lq + P_m)` | `O(n + P_m + J)` context/output | До 128 cards; один generation |
| `build_skills_page()`, `q > 128` | `O(1)` после длины | `O(J)` | Search/sort отсутствуют; query очищен |
| Любой HTTP: invalid/duplicate `Host` | `O(H)` | fixed JSON | До route/body/filesystem; StrictHost экспорт перенесён без новой runtime-цены |
| Security/error middleware success | downstream + `O(1)` | `O(1)` | UUID и фиксированные headers |
| `GET /health` | `O(1)` | fixed JSON | Один короткий sync worker |
| `GET /` | `O(1)` от фиксированного template | fixed HTML | Registry/gate не читаются |
| `GET /api/skills?q`, valid | `O(q + n log n + Lq + P_m)` | `O(P_m + n)` JSON | `m <= 128`, body/issues не выдаются |
| `GET /api/skills?q`, over limit | framework decode + `O(1)` handler | fixed 422 JSON | До sort/search |
| `GET /skills?q`, без marker | `O(q + n log n + Lq + P_m + J)` | `O(P_m + J + n)` HTML | Lock-free current capture |
| `GET /skills?q&reload=x`, marker hit | предыдущая строка + `O(1)` | предыдущая строка | Один из максимум 8 entries атомарно потребляется; `no-store` |
| `GET /skills`, valid unknown/consumed marker | предыдущая строка + `O(1)` | предыдущая строка | Fallback current, без `F`; `no-store` |
| `GET /skills`, malformed marker | предыдущая строка + fixed regex | предыдущая строка | Без state lock; `no-store` |
| `GET /skills?q`, over limit | framework decode + `O(J)` render | `O(J)` HTML | Query не отражается; valid marker уже может быть потреблён |
| POST reload: invalid Origin/Host | `O(H)` | bounded parser state + fixed 403 | До body/gate/`F` |
| POST reload: invalid media type | `O(H)` | `O(1)` | До body/gate/`F` |
| POST reload: invalid/oversized `Content-Length` | `O(header_length)` | `O(1)` | `413` до stream/gate/`F` |
| POST reload: streamed body > 4 096 | до 4 097 payload bytes + chunk overhead | не больше 4 096 накопленных bytes | `413` до gate/`F` |
| POST reload: invalid form/token | `O(b)` | `O(b)` | `b <= 4 096`; bytes `compare_digest`, до gate/`F` |
| POST reload: busy gate | validation + `O(1)` + pool scheduling | fixed 409 | Не ждёт gate; 0 reload workers и 0 `F`; sync error-handler кратко занимает один token после admission лимитера |
| POST reload: flash full (`f == 8`) | validation + `O(1)` + pool scheduling | fixed 409 | Gate кратко взят/освобождён; 0 `F`, 0 CSPRNG; sync error-handler кратко использует pool |
| POST reload: success | validation + `F + O(J+n+K)` | active report + body + 43-byte marker | Не больше одного active/queued pool claimant и одного `F` |
| Отмена admitted POST во время pool/`F` | до завершения максимум одного `F` | одна active load | `checkpoint_if_cancelled` не добавляет flash; gate/locks освобождаются |
| `r` пересекающихся корректных POST, capacity есть | `O(sum(validation_j) + F + J + K + r)` | shared `O(8(M+J))` + request state `O(r)` | Один `F`; `r-1` constant gate-rejection; error handlers могут ждать limiter, но не владеют token во время ожидания |
| `r` пересекающихся POST, flash полон | `O(sum(validation_j) + r)` | request state `O(r)` | Ноль `F`; bounded 409 проходят через короткий sync error-handler |
| Static full GET | `O(p + a)` | bounded file chunk + `a` output | `a <= 232 111`; общий pool |
| Static conditional GET | `O(p)` + stat/header compare | `O(1)` | `304`, без body |
| Static missing/invalid path | `O(p)` | `O(p)` normalization + fixed 404 | Один mounted bounded directory |
| `/openapi.json`, первый вызов | `O(routes + schema)` | bounded cached schema + response | Пять прикладных routes, фиксированные модели |
| `/openapi.json`, повтор | `O(schema_size)` serialize | bounded response | Cached schema |
| `/docs`, `/redoc`, OAuth redirect | `O(1)` server-side | fixed HTML | Registry/gate не читаются |
| Framework 404/405/redirect | `O(p + H)` routing | fixed JSON/redirect | Каталог и `F` не затрагиваются |
| Expected `ReloadUnavailableError` (`errors.py`) | `O(1)` + pool scheduling + log sink | fixed 409 JSON | Handler синхронный: один краткий worker token на фактическое выполнение, ноль token во время ожидания limiter |
| Неожиданная ошибка до response start | downstream + `O(1)` envelope/log | fixed 500 JSON | Context managers освобождают locks |
| `app.js` submit / flash canonicalization | `O(1)` / `O(U)` | `O(1)` / `O(U)` | Browser-only; marker 43 символа |
| `README.md` | runtime N/A | N/A | Документирует единую canonical search |
| Изменённые `tests/*.py` | runtime N/A | N/A | Доказательные сценарии; в этом gate не исполнялись |

# Finding

## P3 — CSPRNG collision-loop не имеет hard bound

Идентификатор: `E1-S4-R2-PERF-001`.

`src/skillhub/web/_catalog.py:169-173` генерирует 32-byte URL-safe marker и
повторяет генерацию, пока значение уже находится в `_flashes`. Capacity
ограничивает множество сравнений семью элементами, выход — 43 символами,
а expected число попыток практически единицей. Однако число повторов самого
`while` не ограничено.

Функция вызывается в `src/skillhub/web/_catalog.py:107-110` под
`threading.Lock`. Поэтому pathological CSPRNG sequence не только удерживает
один reload worker и async admission gate, но и задерживает sync GET с
валидным marker на том же state lock. HTTP-вход не позволяет выбрать CSPRNG
результат, shared память остаётся ограниченной, а вероятность при исправном
256-bit CSPRNG пренебрежимо мала; поэтому это P3, не P1/P2.

Критерий закрытия: задать фиксированное максимальное число генераций и после
исчерпания возвращать контролируемый отказ, гарантированно освобождая
`_state_lock` и `_reload_gate`. Тогда marker-generation получит строгие
`O(1)` time/calls/memory, а не только expected `O(1)`.

# P0–P3

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 1 — `E1-S4-R2-PERF-001`.

**GATE_OK не выдан.**
