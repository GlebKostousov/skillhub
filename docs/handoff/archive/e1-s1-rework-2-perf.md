# Выжимка
Роль: perf
Вердикт: GATE_OK
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0.
R2-PERF-001 закрыта: после получения `EventDict` широкие контейнеры, длинные строки и ключи обрабатываются только до исчерпания node/text budget; дополнительная память, глубина, ссылки и итоговый JSON ограничены.

# Отчёт
Рецепт: да

## Проверка R2-PERF-001

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), прошлый perf-отчёт (`docs/handoff/e1-s1-rework-1-perf.md`) и дельта `cdf166e..96d417f`. Обозначения: `m` — размер широкого mapping; `n/e` — число достижимых узлов/рёбер; `L` — суммарная длина строк и ключей; `k/ℓ` — длина одного ключа/значения; `Bscan` — ограниченный text budget объём реально читаемых символов; `q` — число ASGI-сообщений; `h` — число response headers; `a/A` — число/суммарный размер argv; `g` — существующее process logging state.

- [`src/skillhub/core/_redaction.py:106-118`] заменяет прежнюю полную shallow-copy на двухфазный generator: пять diagnostic lookup константны, `dict.items()` запрашивается лениво, а внешний цикл прекращает запрос элементов сразу при исчерпании бюджета. Для обычного встроенного `dict` число продвижений iterator ограничено; стоимость произвольного переопределённого container iterator его потребителем принципиально не контролируется.
- [`src/skillhub/core/_redaction.py:75-103`] не сериализует исходную строку и не нормализует ключ целиком. `_bounded_text` копирует не более доступного префикса плюс один символ; sensitive-check читает только ограниченные prefix/suffix. Поэтому работа зависит от `min(k/ℓ, remaining_budget)`, а не от полной длины.
- [`src/skillhub/core/_redaction.py:121-217`] учитывает identity контейнеров до спуска: цикл и повторная ссылка дают `[REFERENCE]`; глубина ограничена `16`, число узлов — `256`, text budget — `6000`. После достижения предела каждый активный уровень добавляет не более одного маркера и завершает свой iterator; стек, `seen` и результат не растут от оставшегося входа.
- [`src/skillhub/core/_redaction.py:236-249`] сериализует уже bounded-граф один раз. `ensure_ascii=True` делает число символов равным числу UTF-8-байтов JSON, `allow_nan=False` совместим с узким scalar contract, а результат сверх `MAX_OUTPUT_BYTES = 8192` заменяется фиксированным fallback. Синтаксис JSON, сортировка, маркеры и поля processors входят в финальную проверку.
- Входной `EventDict` уже создан до processor: вызов вида `logger.info(..., **wide_mapping)` неизбежно материализует корневые kwargs за `O(m)` средствами Python. Исправление устранило именно дополнительную `O(m)` копию; широкий вложенный `dict`, переданный одним полем, обходится `O(min(m, budget))`.
- Контрольный прогон на уже созданных входах: mapping из `1_000/100_000/1_000_000` полей занял `0.79/0.32/0.48 ms` при пике `3380/2972/2964 B`; строки `10_000/2_000_000/20_000_000` символов — `0.07/0.09/0.11 ms` при пике до `2648 B`; ключи `10_000/2_000_000` — `0.09/0.13 ms` при пике до `2951 B`. Cycle и глубина `10_000` завершились bounded-маркерами. Проверенные строки JSON с переводом строки были не больше `8192` байт; прямой oversized-вход renderer свёрнут в fallback размером `53` байта (`54` с Windows CRLF).
- Новых накапливающих process loops нет. Uvicorn event loop остаётся владельцем процесса; application wrappers выполняют один callback на ASGI message и не буферизуют body. Process formatter и startup failure делают по одной bounded JSON-сериализации и одному синхронному stderr write на событие. Тестовый health polling имеет deadline `20 s`, request timeout `0.5 s`, постоянную память и завершается вместе с дочерним процессом.

## Complexity

| Path | n/m | O(time) | O(memory), дополнительная | Bound | Вердикт |
|---|---|---:|---:|---|---|
| `_mapping_items` + `_redact_mapping` wide path | `m` полей, `i` реально запрошенных | `O(5 + min(m, i_limit))` average для built-in `dict` | `O(1)` iterator + bounded result | `i_limit <= 255`, остановка по `N=256` или text budget | R2-PERF-001 закрыта |
| `_bounded_text` | строка `ℓ` | `O(min(ℓ, b/12 + 1))` | `O(min(ℓ, b/12 + 1))` | `b <= 6000`; один bounded slice, без полного `json.dumps` | OK |
| `_is_sensitive_field` | ключ `k` | `O(min(k,14) + min(k,9))` | `O(min(k,23))` временно | Не более bounded prefix/suffix; четыре suffix | OK |
| `_redact_value` graph traversal | `n` узлов, `e` рёбер, `L` текста | `O(min(n+e,N) + min(L,Bscan))` average | `O(min(n,N) + B + D)` | `N=256`, `D=16`, text budget `B=6000`; cycles/shared refs через `seen` | OK |
| `render_bounded_json` | bounded `N`, результат `o` | `O(o + N log N)` из-за `sort_keys` | `O(o + N)` | `o <= 8192` после hard fallback; `N=256` | OK |
| Полный structlog processor/render path после получения `EventDict` | `p=5`, bounded `N/o` | `O(p + N log N + o)` | `O(N + B + D + o)` | `p=5`, `N=256`, `D=16`, `o<=8192` | OK |
| Создание root kwargs самим Python | `m` аргументов | `O(m)` | `O(m)` | Вход обязан существовать до processor; дополнительной полной копии больше нет | Неустранимый input cost, не regression |
| `SafeServerFormatter.format` / `_write_startup_failure` | одно process-событие | `O(1)` | `O(1)` | Bounded type/name slices, фиксированный набор полей, один stderr write | OK |
| `configure_server_logging` / `create_app` | `g` process logger/handler state | `O(g)` на `dictConfig` + `O(1)` registrations | `O(1)` новых handlers/config сверх существующего state | Startup/replay only; reconfigure заменяет, а не накапливает handler | OK |
| `TrustedHostEnvelopeMiddleware` | `q` messages, `h` headers start-message | `O(q+h)` | `O(1)` | Один проход headers на response start; body не копируется | OK |
| `ProcessErrorBoundary` | `q` ASGI messages | `O(q)` normal path, `O(1)` до-start fallback | `O(1)` | Один send-wrapper; один bounded log/JSON fallback | OK |
| `_parse_port` + `run_server` | `a/A` argv, один server process | `O(a+A)` startup, затем штатный event loop | `O(a+A)` startup | argv ограничен process ABI; retry/polling в production не добавлены | OK |
| Тестовый `_wait_for_health` / `_stop_process` | число probes/process waits | `O(wall-time)` | `O(1)` без учёта bounded captured logs | deadline `20 s`, probe timeout `0.5 s`, Python waits по `5 s`; один child process, Windows `taskkill` — один внешний вызов без retry loop | OK, test-only |

## Findings

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.
