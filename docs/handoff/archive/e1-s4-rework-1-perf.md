# Выжимка

Роль: perf
Scope: `11393ba..7fe15c7`
Вердикт: **GATE_OK**
Находки P0–P3: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Повторная статическая проверка закрывает прежнюю
`E1-S4-PERF-001`: очередь корректных reload теперь проходит admission на
асинхронном per-app `anyio.Lock` **до** обращения к общему worker pool.
В каждый момент только владелец gate может запустить одну полную файловую
загрузку `F`; остальные POST ожидают в event loop и не занимают worker tokens.
При штатных 40 tokens AnyIO активный reload занимает не более одного, поэтому
для синхронных GET и операций `StaticFiles` остаётся 39.

Запуск benchmark и тестов не выполнялся согласно ограничению задачи. Вывод
основан на delta, текущем коде, зафиксированных AnyIO 4.10.0 / Starlette 1.6.0
и статическом разборе существующих regression-сценариев.

# Что изменилось относительно предыдущего perf gate

Предыдущий путь отправлял каждый конкурентный POST в `run_in_threadpool`, после
чего worker блокировался на `threading.Lock`. При `r >= 40` это позволяло
ожидающим reload занять весь общий пул.

Теперь порядок в `src/skillhub/web/routes.py:79-85` следующий:

1. `validate_reload_request()` проверяет источник, тип, тело и токен.
2. `async with self._reload_gate` ставит корректный запрос в асинхронную
   очередь.
3. Только получивший gate запрос вызывает
   `run_in_threadpool(self._catalog.reload)`.
4. `_CatalogState.reload()` выполняет `SkillRegistry.reload()`, строит
   согласованное поколение, публикует его и обновляет bounded flash.
5. После выхода из gate endpoint создаёт малый `303` на
   `/skills?reload=<marker>`.

Следовательно, для одного экземпляра `_Routes`:

- активных HTTP-вызовов `F` не более одного;
- worker tokens, занятых HTTP reload, не более одного;
- ожидающих worker token из-за reload-gate нет;
- внутренний `SkillRegistry._reload_lock` остаётся защитой не-HTTP callers, но
  конкурентные HTTP reload доходят до него последовательно;
- `GET /health`, `/`, `/api/skills`, `/skills` и static lookup/read не берут
  `_reload_gate` или registry writer-lock.

Очередь не coalescing: `r` принятых и не отменённых POST по-прежнему выполнят
`r` отдельных `F`, дадут суммарную работу `O(rF)` и хвостовую задержку порядка
суммы предыдущих `tau`. Pending request state равен `O(r)`. Это не возвращает
прежнее исчерпание общего worker pool: queued requests ожидают асинхронно, а
каждый запрос и каждая загрузка ограничены ниже. При специально настроенном
pool из одного token активный `F` временно занимает единственный token; штатный
предел 40 оставляет 39.

# Отмена и освобождение блокировок

Есть три существенных случая.

1. **Отмена в очереди gate.** Задача ещё не дошла до `run_in_threadpool`,
   поэтому worker token и registry lock не получены, `F` не запускается.
   AnyIO удаляет отменённого waiter из очереди; следующий живой waiter может
   получить gate. Существующий regression-сценарий
   `test_cancelled_waiting_reload_does_not_run_later` проверяет именно отсутствие
   отложенного loader call.
2. **Отмена владельца во время `run_in_threadpool`.** Starlette делегирует
   вызов в `anyio.to_thread.run_sync` с обычной семантикой
   `abandon_on_cancel=False`: ожидающая coroutine не бросает работающий writer,
   а дожидается worker. После возврата или исключения `async with` освобождает
   `_reload_gate`.
3. **Исключение внутри загрузки.** Внутренний `threading.Lock` находится в
   `with` в `SkillRegistry.reload()`, а внешний gate — в `async with`; оба
   освобождаются при раскрутке стека. Очередь не остаётся навсегда закрытой.

Отключение HTTP-клиента само по себе не прерывает синхронный filesystem syscall.
Это безопаснее для публикации: уже начатый writer заканчивает одну атомарную
публикацию, после чего освобождает token и оба уровня блокировки. Конечной
wall-time гарантии для отдельного syscall по-прежнему нет, но число операций и
объём читаемых данных bounded.

# Обозначения и жёсткие пределы

- `n` — число скиллов в поколении, `0 <= n <= 128`.
- `m <= n` — число результатов поиска / HTML-карточек.
- `i` — число `LoadIssue`, `0 <= i <= 129`; при обычном/частичном обходе
  `n + i <= 129`, root-level отказ даёт `n = 0, i = 1`.
- `q` — длина декодированного поискового параметра; приложение принимает
  `0 <= q <= 128`.
- `body` — накопленное тело reload-формы, `body <= 4 096` байт.
- `B` — суммарные байты `SKILL.md` одной загрузки,
  `B <= 1 048 576`; один файл не более 65 536 байт.
- `S` — суммарная длина публичных `name + caption + description` поколения.
  Caption входит в файловый бюджет, description не более 1 024 code points,
  name не более 64; поэтому `S = O(B + 64n)`.
- `J` — объём безопасных issue `path + reason + message + action`.
  Path не более 128 символов либо фиксированный hash-идентификатор, reason и
  шаблоны сообщений берутся из конечного набора:
  `J = O(i * 128)`, `i <= 129`.
- `A` — просмотренные auxiliary entries: не более 1 024 на обрабатываемый
  skill и не более `128 * 1 024` за загрузку.
- `F` — работа одной полной загрузки; `tau` — её wall time.
- `M` — память полного `LoadReport` одного поколения, включая тела скиллов:
  `O(B + S + n + i + loader overhead)`.
- `r` — число конкурентных корректных reload POST.
- `R` — число успешно завершённых reload за жизнь процесса;
  `d = floor(log10(R)) + 1` — длина серверного marker.
- `p`, `a`, `H` — длина static path, размер asset и объём просмотренных
  заголовков соответственно.

Унаследованные loader bounds не ослаблены:

- не более 256 непосредственных root entries;
- не более 128 попыток каталогов;
- не более 1 024 auxiliary entries на skill и глубина не более 32;
- не более 256 resolution steps и 40 link hops на один `open_child`;
- Windows reparse buffer не более 16 384 байт;
- дополнительные файлы не читаются в память: обход завершается на первом
  допустимом обычном файле либо на budget.

Консервативно
`F = O(K + 256 log 256 + B + A * resolver_cost + n log n)`, где `K` —
ограниченная сумма root-имён. Счётчики ограничивают работу над недоверенными
данными, но не duration отдельного локального или сетевого filesystem syscall.

# Поколения, flash и marker

`_CatalogGeneration` связывает один `LoadReport`, построенный из него
`ReloadSummary` и marker. GET захватывает один указатель и ищет по
`generation.report`, поэтому cards и issues не смешиваются с более новым
registry snapshot.

`_flashes` обновляется как последние 7 элементов плюс новое поколение:

- steady-state содержит максимум 8 поколений;
- `_current` ссылается на тот же объект, что последний flash, и не создаёт
  девятое steady-state поколение;
- registry snapshot текущего поколения ссылается на те же `Skill` objects;
- во время построения нового поколения возможен bounded transient peak:
  8 старых + 1 новое;
- один уже выполняющийся GET может удержать захваченное поколение после его
  eviction до завершения собственного ответа; это обычная bounded
  per-request retention, а не накопление в `_CatalogState`.

Steady-state application retention равен `O(8(M + J) + n)`, peak одного reload
— `O(9M + 8J + J_new + n)`. Число поколений жёстко ограничено; каждое поколение
также ограничено loader budgets.

Marker lookup сначала отклоняет `None`, длину более 20 или не-десятичную строку,
затем линейно просматривает максимум 8 flash entries. Для публичного GET это
`O(20 + 8 * 20) = O(1)` времени и `O(1)` дополнительной памяти. Marker не
содержит CSRF token или файловые данные.

Серверный счётчик `_next_marker` — один монотонный Python integer. Создание
`str(R)` и increment стоят `O(d)` времени и памяти, а удержание счётчика и
восьми marker — `O(8d)`. Это логарифмический рост от числа reload, не рост от
размера каталога или одного запроса. После `d > 20` redirect marker не сможет
пройти публичный `_MAX_MARKER_LENGTH`; пользователь увидит безопасное текущее
поколение без flash-признака. Порог требует не менее `10^20` успешных reload и
не образует реалистичного P0–P3 performance finding для локального процесса.

Если между POST и следованием redirect завершилось более 8 новых reload,
старый marker закономерно evicted: `for_marker()` возвращает текущее поколение
и `reload_performed=False`. Это ограничивает память ценой потери старого flash,
не вызывает повторный scan и остаётся `O(1)`.

# HTML, API и issue bounds

Поиск делает один `query.casefold()`, затем проходит один immutable report.
Консервативный worst case:
`O(q + S + nq)` времени и `O(q + max_field + m)` временной памяти.

`GET /api/skills` строит не более 128 `SkillSummary`, после чего FastAPI
валидирует и сериализует bounded список. Response содержит только `name`,
`caption`, `description`, `has_files`; тела скиллов и issues отсутствуют.
Время и response memory равны
`O(q + S + nq)` и `O(q + max_field + S + n)`.

`GET /skills` строит до 128 карточек и до 129 issue-строк выбранного поколения.
`model_dump()`, Jinja render и UTF-8 response дают
`O(q + S + nq + J)` времени и `O(S + n + J)` дополнительной/response памяти.
Autoescape и UTF-8 увеличивают wire size только постоянным коэффициентом.

При `q > 128` поиск не запускается, исходный query не отражается, cards пусты;
HTML всё ещё может показать bounded `J` выбранного поколения. API возвращает
малый фиксированный JSON 422. Длина всей request line и неизвестных query
parameters остаётся транспортным пределом ASGI server, а не циклом каталога.

Issue summary строится один раз на поколение в том же worker-вызове, что reload.
Для каждой из максимум 129 issues выполняются dictionary lookup и форматирование
bounded safe path; тела, абсолютные пути и YAML не копируются в summary.

Успешный POST больше не рендерит каталог в event loop. Он возвращает короткий
`303`; HTML и поиск выполняет последующий синхронный GET в общем pool, где
лимиты выше применяются обычным образом.

# Static assets

Серверный набор состоит из четырёх локальных файлов:

- Bootstrap: 232 111 байт;
- `app.css`: 701 байт;
- `app.js`: 1 039 байт;
- favicon: 189 байт;
- сумма: 234 040 байт.

Максимальный полный static body фиксирован 232 111 байт. Starlette выполняет
lookup/stat и fallback file I/O через AnyIO thread facilities, отдаёт
`Content-Length`, `Last-Modified`, `ETag`, поддерживает conditional `304` и
читает bounded chunks. Во время reload занят не более один из 40 штатных
tokens; очередь reload больше не может занять остальные 39.

В клиентском `app.js` submit-handler имеет `O(1)`. После flash-страницы
`URLSearchParams.delete/toString` и `history.replaceState` имеют `O(U)`, где
`U` — длина текущего browser URL; серверный marker ограничен 20 символами, а
этот клиентский проход не удерживает server worker или generation.

# Complexity по всем путям

| Path / ветвь | O(time) | Дополнительная O(memory) | Конкурентность / bound |
|---|---:|---:|---|
| `create_app()` / startup success | `F + O(J)` | `O(M + J + n)` | Синхронно до readiness; registry writer-lock на `tau`; одно начальное поколение |
| Startup missing/unreadable root | до первого root failure | `O(1)` | Пустой report, одна issue |
| Любой HTTP: invalid/duplicate `Host` | `O(H)` | `O(number_of_host_headers)` + fixed JSON | До route/body; regex линейный, port conversion только после 5-digit bound |
| Security/error middleware, success | downstream + `O(1)` | `O(1)` | UUID и 5 фиксированных headers |
| `GET /health` | `O(1)` | `O(1)` | Один общий worker token на короткий sync handler; reload оставляет 39 штатных |
| `GET /` | `O(1)` от фиксированного template | фиксированный HTML | Один общий worker token; registry не читается |
| `GET /api/skills?q`, valid | `O(q + S + nq)` | `O(q + max_field + S + n)` | Один захваченный generation, без lock; `q,n <= 128` |
| `GET /api/skills?q`, over limit | framework decode + `O(1)` handler | fixed 422 JSON | Search и issue render отсутствуют |
| `GET /skills?q`, valid, без marker | `O(q + S + nq + J)` | `O(S + n + J)` | Текущее поколение одним указателем; до 128 cards / 129 issues |
| `GET /skills?q&reload=x`, marker hit | предыдущая строка + `O(8 * 20)` | предыдущая строка + `O(1)` lookup | Ровно одно из последних 8 поколений |
| `GET /skills`, invalid/stale marker | предыдущая строка + `O(1)` | предыдущая строка | Fallback к current, `reload_performed=False`, scan/reload отсутствует |
| `GET /skills?q`, over limit | framework decode + `O(J)` render | `O(J)` | Query очищен, search не вызывается, issues <= 129 |
| POST reload: invalid Origin/Host | `O(H)` | bounded parsed origin | До body, gate и filesystem |
| POST reload: invalid media type | `O(H)` | `O(1)` | До body, gate и filesystem |
| POST reload: invalid/oversized `Content-Length` | `O(length_header)` | `O(1)` | `413` до stream/gate |
| POST reload: streamed body > 4 096 | до 4 097 payload bytes + chunk overhead | не более 4 096 накопленных bytes/chunks | `413` до gate; offending transport chunk не копируется |
| POST reload: invalid form/token | `O(body)` | `O(body)` | `body <= 4 096`; до gate/filesystem |
| POST reload: success, gate свободен | `O(body + F + J + d)` | active load peak `O(9M + 9J + n + body + d)` | Gate до pool; один `F`, один worker token; `303` без HTML |
| POST reload: success, gate занят | wall `O(sum(previous tau) + F + J + d)` | `O(1)` bounded request state до владения + active shared state | Ожидание в event loop, 0 worker tokens до admission |
| Отмена ожидающего POST | queue unlink, worst `O(r)` | освобождение его `O(1)` state | `F` не запускается; gate не захвачен |
| Отмена активного POST | уже начатый `F` завершается | peak одной active загрузки | Worker не abandoned; затем освобождаются registry lock и async gate |
| `r` конкурентных корректных POST | total `O(r(F + J + d))` | `O(8(M+J) + r)` steady/backlog; transient одно новое поколение | Не более 1 active `F`, 1 reload worker token, `r-1` async waiters |
| Static full GET | `O(p + a)` | bounded file chunk + transport | `a <= 232 111`; общий pool сохраняет 39 штатных tokens при reload |
| Static conditional GET | `O(p)` + stat/header compare | `O(1)` | `304`, body отсутствует; один короткий pool operation |
| Static missing/invalid path | `O(p)` | `O(p)` normalization | Один mounted bounded directory, fixed 404 envelope |
| `/openapi.json`, первый вызов | `O(Routes + schema)` | cached bounded schema + response | Число routes/models фиксировано, независимо от `n/B/r` |
| `/openapi.json`, повтор | `O(schema_size)` serialize | bounded response | Используется cached schema |
| `/docs`, `/redoc`, OAuth redirect | `O(1)` server-side | фиксированный HTML | Не читают registry; внешние browser assets не server work |
| Framework 404/405/redirect | `O(p + H)` routing | fixed JSON/redirect | Каталог, gate и filesystem не затрагиваются |
| Неожиданная ошибка до response start | downstream + `O(1)` envelope/log | fixed 500 JSON | Context managers освобождают захваченные locks |
| `app.js` submit | `O(1)` | `O(1)` | Отключает локальную кнопку; server gate остаётся авторитетным |
| `app.js` flash canonicalization | `O(U)` | `O(U)` | Только browser URL; marker <= 20 на принимаемом пути |

# P0–P3

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.

**GATE_OK**
