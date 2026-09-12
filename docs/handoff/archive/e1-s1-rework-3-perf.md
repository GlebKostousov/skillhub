# Выжимка
Роль: perf
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Широкий `dict` со строковыми ключами, длинные строки, циклы, глубина, число узлов и итоговый JSON остаются ограниченными. Но сужение redactor внесло обход без budget claim: каждый нестроковый ключ проходит ветку `continue`, не уменьшая `nodes_left` и не расходуя `text_left`; mapping из `m` таких ключей снова даёт `O(m)` времени вместо `O(min(m, budget))`.

# Отчёт
Рецепт: да

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), предыдущий perf gate (`docs/handoff/e1-s1-rework-2-perf.md`) и дельта `b7ea37c..66c20b1`, с фокусом на суженный redactor, позднюю HTTP-границу и startup boundary. Проверка статическая; код и тесты по заданию не запускались и не изменялись.

Обозначения: `m` — ширина mapping/sequence; `u` — число нестроковых ключей mapping; `n/e` — число достижимых узлов/рёбер; `L` — суммарная длина читаемого текста; `ℓ/k` — длина строки/ключа; `N=256` — node budget; `B=6000` — text budget; `D=16` — предел глубины; `q` — число ASGI-сообщений; `h` — число response headers; `r/R` — число/суммарный размер argv или process environment; `o` — размер сериализованного bounded-графа.

## Проверка обязательных случаев

- Wide map со строковыми ключами: `_mapping_items` ленив, семь diagnostic lookup имеют фиксированную стоимость, а каждый принятый элемент вызывает `_redact` и уменьшает `nodes_left`. Обход прекращается не позже исчерпания `N` или `B`: `O(min(m,N,Bscan))` average time и bounded additional memory.
- Wide map с нестроковыми ключами: ветка `src/skillhub/core/_redaction.py:119-121` заменяет пару одним `[UNSUPPORTED]`, но не уменьшает ни один бюджет. При `u=m` цикл читает весь mapping: `O(m)` time, хотя результат имеет постоянный размер. Это R3-PERF-001.
- Long string/key: `_bounded_text` берёт не более `floor(b/12)+1` символов, а sensitive check читает только фиксированные prefix/suffix; полного scan/copy строки нет.
- Cycles/shared references: identity добавляется в `seen` до спуска, повтор даёт `[REFERENCE]`; при строковых ключах и sequence items число попыток посещения ограничено `N`.
- Depth/nodes: контейнер на глубине `D` заменяется `[TRUNCATED]`; стек `O(D)`, `seen` и результат `O(N)`. Исключение — число итераций по нестроковым mapping entries, описанное выше.
- Strict `MAX_OUTPUT_BYTES`: `ensure_ascii=True` делает символы возвращаемого JSON однобайтными в UTF-8; условие `len(rendered)+1 <= 8192` оставляет один байт под логический LF, иначе возвращается фиксированный fallback. До проверки длины сериализуется только граф, ограниченный `N`, `D`, `B` и размером поддерживаемого scalar.
- Late boundary: normal path только оборачивает каждый `send`, не копирует body и не накапливает messages. После `http.response.start` выполняются одно bounded application log event и один raise; внешний formatter может записать ещё одно фиксированное server event. Retry/polling loop не добавлен.
- Startup boundary: `create_process_app()` делает ровно одну попытку `create_app()`; отказ даёт одну сериализацию фиксированного события, один `stderr.write` и `SystemExit`. Новый цикл или повторный I/O отсутствует.

## Complexity

| Path | n/m | O(time) | O(memory), дополнительная | Bound | Вердикт |
|---|---|---:|---:|---|---|
| `_bounded_text` | строка `ℓ`, остаток `b` | `O(min(ℓ,b/12+1))` | `O(min(ℓ,b/12+1))` | `b <= B=6000`; один bounded slice | OK |
| `_is_sensitive_field` | ключ `k` | `O(1)` | `O(1)` | Только фиксированные prefix/suffix и 4 suffix | OK |
| `_mapping_items` + `_redact`, строковые ключи | `m` entries, `n/e`, `L` | `O(min(n+e,N)+min(L,Bscan))` average | `O(N+B+D)` | `N=256`, `B=6000`, `D=16`; не более 7 diagnostic lookup на dict | OK |
| `_redact`, нестроковые mapping keys | `u <= m` | `O(u + min(n+e,N))`, worst `O(m)` | `O(N+B+D)`; repeated marker сам `O(1)` | `u` не связан с `N/B`: budget claim пропущен | P2, R3-PERF-001 |
| `_redact`, wide sequence | `m` items | `O(min(m,N)+min(L,Bscan))` | `O(N+B+D)` | Каждый item уменьшает `nodes_left`; один terminal marker | OK |
| Cycles/shared refs | `n/e` | `O(min(n+e,N))` average | `O(min(n,N)+D)` | `seen`, `[REFERENCE]`, `N=256`, `D=16` | OK |
| Deep container chain | глубина `n` | `O(min(n,D))` | `O(min(n,D))` stack/seen/result | `D=16` | OK |
| `render_bounded_json` | bounded graph `N`, output `o` | `O(o + N log N)` из-за `sort_keys` | `O(o+N)` | Возвращаемый ASCII JSON `<=8191` bytes; с logical LF `<=8192`, иначе fixed fallback | OK |
| Полный structlog path после получения `EventDict` | `m,n/e,L,o` | Для поддерживаемых keys `O(min(input,budget)+N log N)`; при `u=m` — `O(m)` | `O(N+B+D+o)` | Output bounded, но input traversal не bounded для `u` | P2, R3-PERF-001 |
| `ProcessErrorBoundary` | `q` messages | `O(q)` normal; `O(1)` extra на late failure | `O(1)` | Один send-wrapper; body не буферизуется; один bounded app event и один raise | OK |
| `TrustedHostEnvelopeMiddleware` | `q` messages, `h` start headers | `O(q+h)` | `O(1)` | Один header scan на response start; body не копируется | OK |
| `create_process_app` / `_write_startup_failure` | стоимость сборки `r/R` | `O(Cbuild)` success; `O(1)` сверх причины failure | `O(1)` сверх `create_app()` | Одна попытка, один fixed JSON, один stderr write, без retry | OK |
| `_parse_port` / `main` | `r` argv, `R` символов | `O(r+R)` startup | `O(r+R)` parser state | Однократный parse; затем штатный Uvicorn event loop | OK |
| Test-only health/process helpers | число probes/waits | `O(wall-time)` | `O(1)` без captured subprocess output | Deadline `20 s`, request timeout `0.5 s`, process waits `5 s`; production path не изменён | OK |

## Findings

- P0: 0.
- P1: 0.
- [P2] [R3-PERF-001] [`src/skillhub/core/_redaction.py:115-121`] Нестроковый ключ записывается как `[UNSUPPORTED]` и немедленно делает `continue`, не уменьшая `nodes_left` и не расходуя text budget. Поэтому вложенный `dict` из `m` integer/object keys полностью обходится за `O(m)` времени, хотя заявленный node bound равен 256. Конкретное исправление: считать каждую mapping entry budgeted work — перед `continue` уменьшать `nodes_left[0]` на один (либо пропускать `[UNSUPPORTED]` через общий `_redact`/единый claim), чтобы следующая проверка завершала iterator не позже `N`; закрепить counting mapping из 100000 нестроковых ключей проверкой `visited <= 256`.
- P3: 0.
