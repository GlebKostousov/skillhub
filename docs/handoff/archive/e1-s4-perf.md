# Выжимка

Роль: perf
Scope: `e4feefa..f2c6b9f`
Вердикт: **НУЖНА_ДОРАБОТКА**
Находки: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Файловая загрузка, поиск, JSON и HTML имеют жёсткие пределы из E1-S2/E1-S3.
Текущий штатный набор особенно мал: 5 `SKILL.md`, 7 889 байт суммарно,
максимальный файл 1 851 байт и 2 дополнительных файла. Load benchmark не
запускался: для решения gate достаточно статических пределов и анализа
конкурентного пути.

Gate блокирует новый HTTP reload. Каждый корректный
`POST /skills/reload` отправляет полную файловую загрузку в общий AnyIO thread
pool, а `_CatalogState.reload()` удерживает `threading.Lock` на всём scan.
Поэтому burst из 40 и более POST занимает все 40 штатных worker tokens: один
worker читает дерево, остальные ждут тот же lock. В результате ожидают также
синхронные GET-handlers и `StaticFiles`, хотя registry readers сами lock не
берут. Клиентское отключение одной кнопки не ограничивает несколько вкладок,
скриптовые запросы или повтор после сетевой неопределённости.

# Обозначения и пределы

- `n` — число опубликованных/возвращённых скиллов и максимум карточек,
  `0 <= n <= 128`.
- `q` — длина уже декодированного поискового параметра в Unicode code points;
  принимается только `0 <= q <= 128`.
- `body` — фактически прочитанные байты тела reload-формы; успешный путь
  допускает `0 <= body <= 4 096`.
- `i` — число `LoadIssue` в последнем отчёте и максимум строк warning-списка.
  Для текущего loader `0 <= i <= 129`, а в нормальном/частичном обходе
  `n + i <= 129`; root-level отказ даёт `n = 0, i = 1`.
- `m <= n` — число результатов одного поиска.
- `S` — суммарная длина публичных `name + caption + description` текущего
  снимка. Она ограничена суммарным файловым бюджетом плюс короткие имена:
  `S = O(1 048 576 + 64n)`.
- `A` — суммарное число просмотренных auxiliary entries за загрузку:
  не более `1 024` на обрабатываемый skill и не более `128 * 1 024` всего.
- `r` — число одновременных корректных reload POST.
- `F` — работа одной полной файловой загрузки; `tau` — её wall time.
  Счётчики ограничивают объём работы, но не дают deadline отдельному локальному
  или сетевому filesystem syscall.

Унаследованные bounds не ослаблены:

- не более 256 непосредственных root entries;
- не более 128 попыток каталогов;
- не более 65 536 байт одного `SKILL.md`;
- не более 1 048 576 байт всех `SKILL.md` за одну загрузку;
- не более 1 024 auxiliary entries на skill и глубина не более 32;
- не более 256 resolution steps и 40 link hops на один `open_child`;
- Windows reparse buffer не более 16 384 байт.

Дополнительные файлы не читаются в память: дерево обходится только до первого
допустимого обычного файла либо до budget. Приближённо
`F = O(K + 256 log 256 + B + A * resolver_cost + n log n)`, где
`B <= 1 048 576`, а `K` — ограниченная сумма root-имён. Peak loader memory
остаётся `O(B + K + n + i + 1 024 + resolver state)`.

# Startup, reload и lock duration

`create_app()` синхронно вызывает `registry.reload()` до создания доступного
ASGI-приложения. Начальный internal writer-lock удерживается
`tau + O(n log n)` — на открытии/обходе дерева, чтении и YAML parsing,
построении нового mapping и публикации. Конкуренции в штатном startup ещё нет.
Процесс становится готов только после завершения scan; конечной wall-time
границы нет из-за filesystem syscalls, но объём пользовательских данных и число
операций ограничены перечисленными budgets. Для локального каталога это
приемлемая fail-before-ready семантика.

HTTP reload добавляет внешний `_CatalogState._reload_lock` поверх внутреннего
registry lock. Внешний lock удерживается
`tau + O(n log n + i)`: весь scan/publication плюс построение безопасного
`ReloadSummary`. Оба lock всегда берутся в одном порядке, а GET/search их не
берут. После выхода из lock поиск пустой строкой и HTML render выполняются
отдельно; следовательно, lock не охватывает формирование ответа.

Для одного POST peak включает старый snapshot, новый loader/report/snapshot,
summary, публичные модели и готовый HTML:
`O(M_old + M_new + S + n + i)`. Jinja сначала создаёт строку, затем
`HTMLResponse` кодирует bytes, поэтому response-size временно представлен
дважды с постоянным коэффициентом. Старые поколения внутри registry не
накапливаются.

Для `r` последовательных POST работа равна `O(rF)`: endpoint не распознаёт
неизменившееся дерево и каждый раз перечитывает его. Это сохраняет
идемпотентность значения, но не идемпотентность стоимости. Для конкурентных
POST суммарная длительность очереди близка к `sum(tau_j)`; fairness и deadline
`threading.Lock` не обещаны.

# API/UI search, render и output bounds

`SkillRegistry.search(q)` один раз делает `query.casefold()`, затем линейно
сканирует один неизменяемый snapshot. `name.casefold()` вычисляется для каждого
skill, `caption.casefold()` — если name не совпал. Консервативный worst case:
`O(q + S + nq)` времени и `O(q + max_field + m)` дополнительной памяти.
Предвычисленных folded-полей нет, но `n`, `q` и `S` жёстко ограничены.

`GET /api/skills` после поиска создаёт до `n` Pydantic `SkillSummary`, затем
FastAPI валидирует/сериализует список. Время и response memory
`O(search + S + n)`. Тела skill и пути файлов в output не входят.

`GET /skills` дополнительно создаёт `_SkillsPageContext`, делает
`model_dump()` и рендерит до `m <= 128` карточек и до `i <= 129` issue-строк.
Время `O(search + S + n + i)`, дополнительная память и HTML
`O(S + n + i)`. Autoescape может увеличить wire size лишь постоянным
коэффициентом. Over-limit query не отражается и не запускает поиск, но всё ещё
рендерит ограниченный последний issue summary.

После успешного POST `_registry.search("")`, модели и Jinja render вызываются
из `async def reload_skills`; этот ограниченный CPU этап выполняется на event
loop. Worst-case public metadata может приблизиться к файловому megabyte
budget, но максимум 128 карточек и один ответ не образуют самостоятельного
P0–P3 finding. Исправление admission control ниже также исключит конкурентный
шторм таких render.

# CSRF body parsing и query

Reload сначала проверяет единственные `Origin`, `Host` и `Content-Type`, затем
отклоняет неверный/превышенный `Content-Length` до чтения. Stream учитывается
по фактическим байтам; накопленный приложением список содержит не более
`body <= 4 096` байт. Число непустых chunks также не превышает `body`, а
финальный пустой chunk добавляет только одну константную запись.

UTF-8 decode и `parse_qsl(..., max_num_fields=2)` имеют `O(body)` времени и
памяти. Строгий контракт принимает ровно одну пару `csrf_token`; compare
остаётся `O(body)`. Все rejected paths завершаются до filesystem reload.

Предел `q <= 128` применяется после framework URL decoding. Сам search и
отражённый query ограничены; размер всей HTTP request line/неиспользуемых query
параметров остаётся транспортным пределом ASGI-сервера, а не этого handler.
Для штатного Uvicorn/h11 это не создаёт нового неограниченного прикладного
цикла.

# Static assets

HTML ссылается только на локальные файлы. Размеры: Bootstrap — 232 111 байт,
`app.css` — 382, `app.js` — 583, favicon — 189; полный набор — 233 265 байт
плюс HTML. GZip middleware нет. Starlette отдаёт `Content-Length`,
`Last-Modified`, `ETag`, поддерживает conditional `304` и читает файл chunks
по 64 KiB; явного `Cache-Control` приложение не задаёт.

Cold transfer Bootstrap заметно больше остальных ресурсов, но фиксирован,
локален и versioned. Повторный запрос может быть условным и не передавать body.
Отсутствие explicit freshness создаёт revalidation/stat, но при локальном bind
и фиксированных 233 265 байтах не достигает P0–P3. Важно, что lookup/stat и
fallback file reads используют тот же AnyIO thread pool и потому попадают под
найденное reload starvation.

# Complexity по всем публичным путям

| Path | O(time) | Дополнительная O(memory) | Lock / жёсткий bound |
|---|---:|---:|---|
| `create_app()` / startup success | `F + O(i)` | `O(M_new + n + i)` | Internal writer-lock на `tau + O(n log n)`; readiness ждёт scan |
| Startup missing/unreadable root | работа до первого отказа | `O(1)` issue + app state | Публикуется пустой snapshot; один root issue |
| `GET /health` | `O(1)` | `O(1)` | Sync handler использует общий thread pool |
| `GET /` | размер фиксированного template | размер фиксированного HTML | Sync handler; registry не читается |
| `GET /api/skills?q`, valid | `O(q + S + nq)` search + `O(S+n)` serialize | `O(q + max_field + S + n)` | Lock-free snapshot; `q <= 128`, `n <= 128` |
| `GET /api/skills?q`, over limit | framework decode + `O(1)` handler | error JSON | Нет search; 422 |
| `GET /skills?q`, valid | `O(q + S + nq + i)` | `O(S+n+i)` | До 128 cards и 129 issues; sync handler |
| `GET /skills?q`, over limit | framework decode + `O(i)` render | `O(i)` | Query не отражается, search не вызывается |
| Чтение `_CatalogState.summary` | `O(1)` | `O(1)` | Lock-free ссылка |
| POST: invalid Origin/Host | длина headers/origin | bounded parsed origin | До body и reload не доходит |
| POST: invalid media type | длина Content-Type | `O(1)` | До body и reload не доходит |
| POST: oversized/invalid declared length | длина header | `O(1)` | 413 до stream |
| POST: streamed oversized body | `O(4 097)` до отказа, кроме размера уже доставленного chunk | накоплено не более 4 096 байт | 413 до reload |
| POST: invalid form/token | `O(body)` | `O(body)` | `body <= 4 096`, до reload |
| POST reload success | `O(body + F + q + S + n + i)` при `q=0` | `O(M_old+M_new+S+n+i+body)` peak | Outer lock `tau + O(n log n+i)`; затем render без lock |
| `r` последовательных reload | `O(rF)` | peak одной операции без внешнего retention | Каждая операция повторяет scan |
| `r` конкурентных reload | `O(rF)` work, `O(r)` pending request state | до `min(r,40)` занятых worker tokens + `O(r)` queue | Один active loader, остальные workers ждут lock; **P2** |
| `app.js` submit handler | `O(1)` | `O(1)` | Отключает одну DOM-кнопку; server admission не задаёт |
| Static first/full GET | path/stat + `O(asset_bytes)` | file chunk до 64 KiB + transport | Общий thread pool; самый большой asset 232 111 байт |
| Static conditional GET | path/stat + header compare | `O(1)` | `304`, но stat всё равно нужен |
| Static missing/invalid path | path normalization + bounded directory lookup | `O(path)` | Общий thread pool; один mounted directory |
| Security/error middleware | `O(1)` поверх downstream | `O(1)` | 5 фиксированных headers, bounded error JSON |

# Finding

## E1-S4-PERF-001 — P2 — reload burst исчерпывает общий worker pool

**Evidence.** `src/skillhub/web/routes.py:121-126` держит внешний
`threading.Lock` на полной `SkillRegistry.reload()`. Строки 243-245 вызывают
этот путь через `starlette.concurrency.run_in_threadpool()` без admission
control, timeout или coalescing. В зафиксированном AnyIO 4.10 default limiter
имеет 40 tokens. FastAPI запускает все `def` handlers в том же pool, а
Starlette `StaticFiles` использует его для lookup/stat и файлового чтения.

**Impact.** При `r >= 40` корректных параллельных POST один worker выполняет
bounded, но потенциально медленный filesystem scan, а остальные 39 блокируются
на `_reload_lock`. `/health`, `/`, `/skills`, `/api/skills` и static lookup не
могут получить token. При большой конечной очереди GET ждёт до завершения
многих полных scan; без server concurrency limit число pending requests и их
память растут как `O(r)`. Повторный POST семантически идемпотентен, но вызывает
новую `O(F)` работу.

**Severity.** P2, не P1: деградирует доступность всего локального процесса, но
маршрут доступен только через localhost/trusted host и требует актуальные
same-origin/CSRF данные; объём одной загрузки жёстко ограничен.

**Конкретное исправление.**

1. Перед входом в общий thread pool добавить per-app асинхронный
   non-blocking single-flight gate.
2. Если reload уже active, не ставить ещё один filesystem scan в очередь:
   немедленно вернуть `409` или `429` с ограниченным ответом и при необходимости
   `Retry-After`, либо присоединить caller к одному уже созданному result task.
3. Только владелец gate вызывает `_CatalogState.reload()`; internal registry
   lock оставить как защиту для не-HTTP callers. Дополнительно можно выделить
   reload собственный `CapacityLimiter(1)`, чтобы будущая ошибка admission не
   заняла общий pool.
4. Критерий регрессии: при остановленном внутри loader первом POST burst
   последующих валидных POST не увеличивает число loader calls и не мешает
   `/health` и static response завершиться до release первого.

# P0–P3

- P0: 0.
- P1: 0.
- P2: 1 — `E1-S4-PERF-001`.
- P3: 0.
