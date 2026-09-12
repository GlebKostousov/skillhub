# Выжимка
Роль: security
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 2 — P0: 0, P1: 0, P2: 1, P3: 1

AppSec: ДА. LLM: НЕТ. CLI-путь безопасно закрывает неверные argv и конфигурацию, оба проверенных localhost bind failure дают только фиксированные lifecycle-коды, а штатные HTTP 400/404/405/500 имеют безопасный envelope и server-generated request ID. Но опубликованный `skillhub.main:app` при неверной конфигурации завершается полным Python traceback вместо фиксированного startup-события, а `ProcessErrorBoundary` молча поглощает исключение после `http.response.start` без прикладного события и корреляции.

# Отчёт
Рецепт: да

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), исходный security-отчёт (`docs/handoff/e1-s1-security.md:1-78`), прошлый rework security-отчёт (`docs/handoff/e1-s1-rework-1-security.md:1-90`) и дельта `cdf166e..96d417f`. Фактические проверки: `pytest -q` — 72 passed; Ruff, format-check, mypy strict и import-linter прошли; `git diff --check cdf166e..96d417f` чист; ограниченный secret scan дерева и истории совпадений не нашёл.

## Фактический data flow

1. Документированный путь `uv run python -m skillhub --port 8000` делегируется из `skillhub.__main__` в `_server.main()` (`README.md:7-17`, `src/skillhub/__main__.py:1-8`).
2. `_server.main()` сначала разбирает ограниченный порт, затем внутри одной process boundary вызывает `create_app()`; любое исключение до запуска Uvicorn преобразуется в фиксированный JSON без `str(exc)`, `repr(exc)` и exception chaining (`src/skillhub/_server.py:29-52`, `src/skillhub/_server.py:55-81`, `src/skillhub/_server.py:95-103`).
3. `create_app()` валидирует `Settings`, включает structlog и safe Uvicorn logging, затем собирает middleware в порядке, при котором `ProcessErrorBoundary` владеет неожиданными ASGI-ошибками, а `SecurityHeadersMiddleware` — request ID и заголовками (`src/skillhub/app_factory.py:20-46`).
4. `_server.run_server()` фиксирует `127.0.0.1`, отключает access log и передаёт safe log config (`src/skillhub/_server.py:84-92`). Реальные bind-пробы обоих entry дали только `process_started`, `application_starting`, `application_started`, `bind_failure`, `application_stopping`, `application_stopped`.
5. Второй опубликованный путь загружает `skillhub.main:app` стандартным Uvicorn import; `main.py` без process boundary сразу исполняет `create_app()` (`src/skillhub/main.py:1-5`). При ошибке `Settings()` выполнение не достигает `configure_server_logging()` (`src/skillhub/app_factory.py:28-30`), поэтому исключение уходит в raw process stderr.
6. Для принятого HTTP-запроса `SecurityHeadersMiddleware` генерирует `uuid4().hex`, сохраняет его в `request.state`/contextvars и добавляет в ответ (`src/skillhub/web/security.py:34-59`). Host, framework и expected handlers пишут только фиксированные code/status (`src/skillhub/web/_boundaries.py:41-88`, `src/skillhub/web/errors.py:55-83`).
7. Unexpected exception до начала ответа попадает в `_unexpected_error_response`: лог содержит фиксированные event/type/code/status/request ID, клиент получает безопасный 500 (`src/skillhub/web/_boundaries.py:20-39`, `src/skillhub/web/_boundaries.py:103-123`). После начала ответа срабатывает отдельная проблемная ветка `return`.

## A. Вход и валидация

- Собственный CLI не отражает неверный argv: `_SafeArgumentParser.error()` отбрасывает сообщение argparse, а порт допускается только в диапазоне `1..65535` (`src/skillhub/_server.py:29-52`). Process-тесты покрывают строковый port, неизвестный аргумент и ноль (`tests/test_readme_startup.py:284-314`, `tests/test_readme_startup.py:360-384` в `96d417f`).
- `Settings` принимает только `development|test|production`, не читает `.env`, скрывает input в validation errors и через строгий источник превращает неизвестный `SKILLHUB_*` suffix в запрещённое extra-поле (`src/skillhub/core/settings.py:12-37`, `src/skillhub/core/settings.py:41-55`).
- Документированный entry на неверной конфигурации выдаёт только `startup.configuration_error`/`invalid_configuration` (`src/skillhub/_server.py:55-81`).
- Опубликованный entry fail-closed по exit status, но не по безопасному представлению ошибки: подтверждено finding `SEC-E1S1-R2-001`.

## B. Authz, capabilities и tenant

N/A — слайс содержит только локальные read-only root/health, без пользователей, tenant-данных, ролей, делегируемых полномочий и изменяющих операций. Сетевая граница проверена в F.

## C. Секреты, пользовательский контент и логи

- `SafeServerFormatter` не форматирует `record.msg`, args или traceback; из записи выводятся только фиксированные diagnostic code/event/level/time и безопасный error type (`src/skillhub/_server_logging.py:38-76`). `uvicorn.access` отключён и лишён handlers (`src/skillhub/_server_logging.py:81-107`).
- Structlog принимает только bounded dict/list/tuple/scalars, рекурсивно закрывает известные секретные и пользовательские поля, циклы и повторные ссылки до JSON renderer (`src/skillhub/core/_redaction.py:11-51`, `src/skillhub/core/_redaction.py:95-117`, `src/skillhub/core/_redaction.py:120-216`).
- Текущие HTTP call sites не передают query/path/Host/body в события; unexpected handler использует только имя класса и фиксированные поля (`src/skillhub/web/_boundaries.py:20-39`, `src/skillhub/web/errors.py:55-83`).
- Process-пробы query и unexpected exception для документированного entry закреплены тестами (`tests/test_readme_startup.py:231-255`, `tests/test_readme_startup.py:429-464` в `96d417f`).
- Для опубликованного entry проба `SKILLHUB_ENVIRONMENT=private-external-env-9361 uvicorn skillhub.main:app ...` не раскрыла маркер благодаря `hide_input_in_errors`, но вывела полный traceback, абсолютные пути и внутренние frames; безопасного startup JSON не было. Это `SEC-E1S1-R2-001`.

## D. Injection и файлы

N/A для SQL, shell, YAML, upload и пользовательских путей — таких входов в E1-S1 нет. Пути шаблонов/статики строятся из фиксированного package path (`src/skillhub/app_factory.py:17-43`), а root передаёт шаблону только объект запроса (`src/skillhub/web/routes.py:20-52`).

## E. Криптография

N/A — credentials, токенов, шифрования и собственного key management в дельте нет. `uuid4().hex` используется только как серверный correlation ID, не как credential (`src/skillhub/web/security.py:51-56`).

## F. HTTP, Host, CORS, CSP, process lifecycle и unexpected boundary

- Поддерживаемый launcher жёстко привязан к `127.0.0.1`; второй entry при стандартном Uvicorn запуске также проверен с localhost, но README честно оставляет его network options внешнему runner (`src/skillhub/_server.py:84-92`, `README.md:14-18`).
- Реальные занятые-port пробы обоих entry дали санитизированный `bind_failure` без адреса, raw exception и traceback. Lifecycle mapping и formatter определены в `src/skillhub/_server_logging.py:9-18` и `src/skillhub/_server_logging.py:38-76`.
- TrustedHost допускает только `127.0.0.1`, `localhost`, `testserver`; plain-text 400 заменяется безопасным JSON (`src/skillhub/app_factory.py:31-38`, `src/skillhub/web/_boundaries.py:41-88`).
- 404/405 нормализуются в стабильный envelope; CORS middleware отсутствует (`src/skillhub/web/errors.py:11-16`, `src/skillhub/web/errors.py:34-83`). Все ASGI-ответы получают CSP, `nosniff`, `no-referrer`, `DENY` и server-generated `X-Request-ID` (`src/skillhub/web/security.py:11-30`, `src/skillhub/web/security.py:34-59`).
- Unexpected exception до response start безопасно закрыта и коррелируется. После response start boundary выполняет `return`, не вызывая безопасный logger и не завершая body (`src/skillhub/web/_boundaries.py:103-123`): `SEC-E1S1-R2-002`.
- Ошибки HTTP parser до входа в ASGI остаются Uvicorn boundary, поэтому проектный JSON/request ID к ним неприменим; raw malformed-socket regression test отсутствует.

## G. Деньги, replay и idempotency

N/A — платных вызовов, retries, изменяющих операций и persistent state нет. Повторные health-запросы имеют одинаковый `{"status":"ok"}`, но независимые server-generated request ID (`tests/test_web.py:105-130` в `96d417f`).

## H. DoS и строгие bounds

- Scrubber ограничен глубиной 16, 256 узлами, консервативным текстовым бюджетом 6000 и итоговым ASCII JSON вместе с newline не более 8192 байт (`src/skillhub/core/_redaction.py:11-20`, `src/skillhub/core/_redaction.py:55-88`, `src/skillhub/core/_redaction.py:238-249`).
- Traversal останавливается до дальнейшего спуска; повторные identity и циклы становятся `[REFERENCE]`; длинная строка режется до небольшого prefix без полной сериализации (`src/skillhub/core/_redaction.py:75-88`, `src/skillhub/core/_redaction.py:120-174`).
- Тесты проверяют depth/nodes/output cap и peak allocation для mapping из 100000 полей и строки из 2 MB (`tests/test_logging.py:117-166` в `96d417f`).
- Root/health не читают request body и выполняют константную локальную работу (`src/skillhub/web/routes.py:30-52`). Application-level rate, connection, header и body limits отсутствуют; основной compensating control текущего слайса — localhost bind.

## I. XSS, client tokens, assets, supply-chain и Git ignore

- Шаблон содержит статический текст и локальные asset URLs; пользовательских значений, JavaScript, browser storage и client token flow нет (`src/skillhub/web/templates/home.html:1-23`).
- Bootstrap обслуживается локально; версия и SHA-256 закреплены README (`README.md:38-43`). Dependencies/lock в проверяемой дельте не менялись.
- `.env`, `.env.*`, `*.key`, `*.pem`, logs, temp и worktree/agent artifacts игнорируются (`.gitignore:1-6`, `.gitignore:42-50`), а repository test покрывает основные patterns (`tests/test_repository_contract.py:33-66`).
- `git diff --check` чист, все tracked index-тексты имеют `i/lf`; ограниченный scan распространённых private-key/API-token signatures в дереве и истории совпадений не нашёл.

## J. LLM: N/A — модельного data flow нет

LLM-scope: НЕТ. В дельте отсутствуют model client/gateway, prompt assembly, передача `material`/`protocol` модели, tool calling, парсинг model output и исполнение содержимого скиллов. Prompt injection и полномочия модели не имеют достижимого потока.

## Attack-test gaps

- Нет process-теста неверной/лишней `SKILLHUB_*` конфигурации через опубликованный `uvicorn skillhub.main:app`; существующий тест второго entry проверяет только успешный запрос с query-маркером (`tests/test_readme_startup.py:257-282` в `96d417f`).
- Нет attack-теста исключения после `http.response.start`. Выполненный ASGI probe получил только `http.response.start` и нормальный возврат boundary — без body и `http.unexpected_error`; repository test покрывает только отказ до старта ответа (`tests/test_web.py:211-238` в `96d417f`).
- Нет spawned-process unexpected-500 теста именно для опубликованного entry; процессная проверка добавляет ошибочный маршрут только через `_server.run_server()` (`tests/test_readme_startup.py:119-144`, `tests/test_readme_startup.py:429-464` в `96d417f`).
- Нет raw-socket regression tests для malformed HTTP, oversized headers/body, slow client и burst. До-ASGI отказ parser не получает проектный request ID.
- Redactor не атакован subclasses встроенных `dict/list/str/int`, custom mapping key behavior, управляющими символами и конфликтующими normalized keys; текущий production HTTP flow такие объекты в logger не передаёт.
- Git ignore не является secret boundary: `*.p12`, `*.pfx` и `id_rsa` текущими patterns не закрыты. Нет entropy scan, полного credential scanner и проверки всех private-key/container форматов.

## Findings

P0: 0
P1: 0

- [P2] [SEC-E1S1-R2-001] [src/skillhub/main.py:1-5, src/skillhub/app_factory.py:28-30, README.md:14-18] Опубликованный `skillhub.main:app` создаёт приложение на import до установки safe server logging и не имеет startup exception boundary. Фактический запуск с неверным `SKILLHUB_ENVIRONMENT` завершился полным Python traceback с абсолютными путями и внутренними frames; фиксированное `startup.configuration_error` отсутствовало. Текущее неверное значение скрыто Pydantic, но любой иной build/import exception может дополнительно раскрыть своё сообщение. → Обернуть создание опубликованного app общей process-safe startup boundary, которая пишет только фиксированные event/code/type и завершает импорт без exception chaining; добавить process-тест invalid/unknown `SKILLHUB_*` через стандартную загрузку Uvicorn с проверкой отсутствия marker, traceback и абсолютного repo path.

- [P3] [SEC-E1S1-R2-002] [src/skillhub/web/_boundaries.py:103-123] После `http.response.start` `ProcessErrorBoundary` поглощает любое `Exception` простым `return`; `_unexpected_error_response` не вызывается, безопасное событие с request ID/error type/code отсутствует, а ASGI body остаётся незавершённым. Late streaming/static/send failure теряет прикладную audit-корреляцию и выглядит для внешней границы как нормально вернувшийся callable либо как некоррелированный generic server failure. → Для ветки `response_started` записывать bounded безопасное событие с существующим request ID и фиксированным code/type, затем явно передавать abort внешней server boundary без raw exception content; закрепить ASGI и spawned-Uvicorn тестами, что соединение завершается, приватный marker не попадает в stderr, а safe event содержит тот же request ID.
