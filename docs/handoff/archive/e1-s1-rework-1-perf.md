# Выжимка
Роль: perf
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 1 — P2 PERF-001: graph-aware обход ограничен, но полный scrubber всё ещё делает неограниченную по ширине копию события и линейные временные копии длинных строк до применения бюджета.

# Отчёт
Рецепт: да

## Анализ

Проверены договор E1-S1, прошлый `e1-s1-perf.md` и фактическая дельта `e8b11d7..cda20c3`. Обозначения: `m` — число полей верхнего уровня события; `n/e` — число уникальных достижимых значений/рёбер; `d` — глубина; `L` — суммарная длина реально просканированных строк и ключей; `B` — текстовый бюджет scrubber; `q` — число ASGI-сообщений; `h` — число HTTP-заголовков; `v` — число переменных окружения.

- Циклы закрыты: `seen: set[id]` ставит `[REFERENCE]` до повторного спуска. Повторные ссылки раскрываются один раз, последующие рёбра дают маркер. Глубина ограничена `16`, число вызовов `_redact_value` — `256`; стек и результирующий граф больше не растут экспоненциально.
- Возвращаемая структура имеет постоянный верхний предел через `B = 8192`, `n = 256`, `d = 16`, однако `B` считает JSON-символы только ключей/скаляров. Синтаксис контейнеров, маркеры, добавленные после scrubber `level/timestamp` и UTF-8-расширение не учтены, поэтому это не строгий предел итоговых байтов.
- Контрольный прогон подтвердил остановку на cycle, shared reference и глубине 100. Но событие из 100 000 полей дало `7.33 MiB` пиковой дополнительной памяти при результате `2725` символов, а строка из 10 000 000 символов — `9.54 MiB` при результате `1378` символов.

- [P2] [PERF-001] [`src/skillhub/core/logging.py:96,157,271-279,358`] Полный путь `redact_sensitive_fields` остаётся `O(m + L)` по времени и `O(m + ℓmax + min(n, 256) + B + d)` по дополнительной памяти, несмотря на фиксированные лимиты обхода. `_diagnostics_first` сначала полностью копирует все `m` полей в новый `dict`, а `_bounded_text` выполняет `json.dumps` всей строки длины `ℓ`; для ключа затем ещё раз строится полная нормализованная строка в `_is_sensitive_field`. Эти операции происходят до эффективной остановки по budget, поэтому широкое событие или один длинный неизвестный scalar по-прежнему линейно раздувают CPU/heap глобального logging processor. Исправление: выдавать diagnostic-поля и остальные `event_dict.items()` ленивым двухфазным iterator без промежуточного `dict` и прекращать iterator сразу по node/text budget; для строк/ключей считать JSON/UTF-8 стоимость посимвольно только до остатка бюджета, резервируя место под маркер, без полного `json.dumps`; sensitive suffix проверять по ограниченному хвосту уже полученного строкового ключа. Финальный renderer должен учитывать JSON-разделители, маркеры и фиксированные поля и гарантировать один явный `MAX_OUTPUT_BYTES`, после чего сериализовать результат один раз.

## Сводка complexity

| Путь / операция | n/m | O(time) | O(memory) | Bound | Bottleneck / вердикт |
|---|---|---:|---:|---|---|
| `core/logging.py:_diagnostics_first` | `m` верхнеуровневых полей | `O(m)` | `O(m)` | Нет; выполняется до `256/8192` | Полная shallow-copy широкого event — PERF-001 |
| `core/logging.py:_redact_value/_redact_mapping/_redact_sequence` | `n` значений, `e` рёбер, `d` глубина | `O(min(n+e, Nmax) + K)` после получения root, где `K` ограничен key budget | `O(min(n, Nmax) + B + d)` | `Nmax=256`, `dmax=16`, `B=8192`; cycles/references bounded | Сам graph-aware обход закрывает прежний exponential/RecursionError path — OK |
| `core/logging.py:_bounded_text/_is_sensitive_field` | `ℓ` длина одной строки/ключа, `L` суммарно | `O(L)` | `O(ℓmax)` временно | Входные `ℓ/L` не ограничены; выход ограничен консервативным prefix | Полный `json.dumps`, затем полная `lower().replace()` ключа — PERF-001 |
| `core/logging.py:redact_sensitive_fields` end-to-end | `m,n,e,L` | `O(m + L + min(n+e,256))` | `O(m + ℓmax + 256 + B + 16)` | Итоговая структура bounded, но не строгими `8192` байтами | Pre-budget work доминирует — PERF-001 |
| `core/logging.py:structlog processors + renderer + PrintLogger I/O` | `p=5`, `o` байт результата | `O(p + o)` CPU + `O(o)` синхронный write | `O(o)` | `o = O(B + Nmax + dmax)`, но точного byte cap нет; access-log отключён | Один bounded event; backpressure stdout/stderr блокирует event loop на error path — приемлемо для локального S1 после PERF-001 |
| `core/logging.py:SafeServerFormatter.format` | 2 поля lifecycle / 4 поля error | `O(o)` | `O(o)` | Поля фиксированы, кроме code-defined имени класса | Один JSON render без `record.getMessage()` — OK |
| `core/logging.py:build_server_log_config/configure_logging` | `p=5`, 2 logger config | `O(p)` | `O(p)` | Константный набор | Только startup/reconfigure — OK |
| `core/logging.py:bind_request_id/clear_request_context` | `c` structlog ContextVars текущего context | `O(c)` | `O(c)` служебных token/reset операций | Приложение привязывает 1 ключ на запрос | Линейный clear, текущий `c=1` — OK |
| `core/settings.py:_StrictEnvironmentSource.__call__` | `v` env entries, `f` fields/aliases, `V` символов env | `O(V + f + v log v)` | `O(v + f)` | Startup; `f=1`, process env ограничен ОС | `sorted(self.env_vars)` доминирует, но не request hot path — OK |
| `web/errors.py:_error_response/_framework_error_response` | фиксированное body, `h` переданных headers | `O(h + b)` | `O(h + b)` | 3 известных status mapping; `b` фиксирован, `h` задаёт framework exception | JSON normalization линейна по фактическому ответу — OK |
| `web/errors.py:TrustedHostEnvelopeMiddleware` | `a=3` allowed hosts, Host длины `k`, `q` messages, `h` response headers | `O(a·k + q + h)` | `O(h)` на response-start | `a=3`; текущие response headers малы; `q` зависит от stream | `dict(message["headers"])` копирует все headers один раз на ответ; текущий bound мал — OK |
| `web/errors.py:ProcessErrorBoundary` | `q` ASGI messages | `O(q)` normal path, `O(1)` fallback + bounded log/JSON | `O(1)` сверх ответа | Один wrapper callback на message; fallback один раз | Не буферизует body, только отслеживает start — OK |
| `web/errors.py:unexpected_error_handler` | один log event и один фиксированный JSON | `O(1)` CPU без sink latency | `O(1)` | Один вызов на exception | Синхронный bounded log write — OK для текущего локального режима |
| `__main__.py:_parse_port/_write_startup_failure/run_server` | `a` argv, один startup event | `O(a)` parsing + `O(1)` event/write | `O(a)` | Однократно на процесс; port `1..65535`, event фиксирован | Startup stderr I/O и Uvicorn loop, повторного/накапливаемого состояния нет — OK |
| `app_factory.py:create_app` + registrations | `r` routes, `w=2` middleware, `x=3` handlers | `O(v log v + r + w + x)` | `O(v + r + w + x)` | Текущие `r/w/x` фиксированы; один static directory metadata check | Environment scan/sort — startup bottleneck; request loop не затронут — OK |
