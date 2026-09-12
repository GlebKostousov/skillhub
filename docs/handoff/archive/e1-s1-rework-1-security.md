# Выжимка

Роль: security
Вердикт: GATE_OK
Проверенная дельта: `git diff e8b11d7..cda20c3`
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0

Три прежние находки закрыты фактическими потоками: документированный Uvicorn-процесс не пишет raw access/error/traceback и не передаёт exception серверу; startup-конфигурация завершается санитизированным событием без исходного значения; неизвестные `SKILLHUB_*` переменные приводят к fail-closed отказу. HTTP 400 для Host, 404 и 405 имеют единый JSON-envelope, защитные заголовки и коррелируемый request ID. Рекурсивная зачистка покрывает `protocol`, пользовательские поля, циклы, повторные ссылки и ограничение результата. Git ignore закрывает `*.tmp`.

# Отчёт

Рецепт: да
Security-scope: ДА — процессный запуск, HTTP/Host, конфигурация, секреты и логи, CSP/CORS, request ID, пределы scrubber и Git ignore.
LLM-scope: НЕТ — в E1-S1 отсутствуют модельный gateway, prompt/material flow к модели, tool calling и обработка модельного ответа.

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), прежний отчёт (`docs/handoff/e1-s1-security.md:1-78`), commit range `e8b11d7..cda20c3`, все изменённые code/tests и фактический process/socket flow. `uv run --no-sync pytest -q`: 62 passed; Ruff, mypy strict и import-linter прошли; `git diff --check e8b11d7..cda20c3` чист.

## Фактический data flow

1. README направляет только в `uv run python -m skillhub --port 8000` (`README.md:7-17`).
2. `skillhub.__main__.main()` разбирает ограниченный порт, собирает приложение внутри process-level `try`, а любой startup exception переводит в JSON-событие только с фиксированными `event`, `error_code` и именем типа (`src/skillhub/__main__.py:17-51`, `src/skillhub/__main__.py:71-79`).
3. Успешная сборка проходит через `Settings()` до настройки приложения, затем включает structlog и middleware (`src/skillhub/app_factory.py:20-43`).
4. `run_server()` оборачивает приложение во внешнюю `ProcessErrorBoundary`, привязывает только `127.0.0.1`, отключает access log и передаёт безопасную конфигурацию stdlib/Uvicorn-логов (`src/skillhub/__main__.py:54-67`).
5. Для HTTP-запроса `SecurityHeadersMiddleware` генерирует серверный `uuid4().hex`, помещает его в state/context и добавляет в ответ; далее TrustedHost и handlers пишут только фиксированные code/status/type (`src/skillhub/web/security.py:34-59`, `src/skillhub/web/errors.py:95-100`, `src/skillhub/web/errors.py:126-134`).
6. При неожиданной ошибке handler формирует безопасный 500 и пишет type/code/status/request ID без exception value (`src/skillhub/web/errors.py:137-165`). После отправки ответа Starlette может повторно поднять exception, но внешняя process boundary видит уже начатый ответ и не передаёт exception Uvicorn (`src/skillhub/web/errors.py:220-276`).

## A. Input и валидация

- Допустимое окружение ограничено `development|test|production`; `.env` отключён (`src/skillhub/core/settings.py:12`, `src/skillhub/core/settings.py:41-56`).
- `_StrictEnvironmentSource` строит allowlist объявленных env-имён и подаёт неизвестный `SKILLHUB_*` suffix как extra input; `extra="forbid"` завершает конфигурацию отказом (`src/skillhub/core/settings.py:15-37`, `src/skillhub/core/settings.py:47-54`).
- `hide_input_in_errors=True` скрывает исходное значение в текстовом `ValidationError`, а process boundary вообще не форматирует exception (`src/skillhub/core/settings.py:51-53`, `src/skillhub/__main__.py:33-51`).
- Независимая process-проверка `SKILLHUB_UNEXPECTED=private-unknown-prefixed-5517` дала exit 2, `startup.configuration_error`/`invalid_configuration`, без маркера и traceback.
- HTTP-ввод E1-S1 не передаётся в прикладные логи: root и health не читают query/body (`src/skillhub/web/routes.py:30-52`), framework handlers не читают request path/Host/body.

## B. Authz, capabilities и tenant

N/A — локальный read-only каркас не содержит пользователей, tenant-данных, ролей, делегируемых полномочий и изменяющих маршрутов. Сетевая граница проверена в F.

## C. Секреты и логи

- `SafeServerFormatter` полностью отбрасывает `record.msg`, args и traceback; для ошибки остаются фиксированные event/code/level и имя класса exception (`src/skillhub/core/logging.py:282-309`).
- Конфигурация Uvicorn направляет его и дочерний `uvicorn.error` в safe formatter, отключает propagation; `uvicorn.access` не имеет handlers, а `access_log=False` отключает генерацию access-событий (`src/skillhub/core/logging.py:312-340`, `src/skillhub/__main__.py:61-67`).
- Spawned-process тест посылает query-маркер и подтверждает его отсутствие в полном stdout/stderr (`tests/test_readme_startup.py:182-207`). Другой spawned-process тест поднимает `RuntimeError` с приватным маркером и подтверждает безопасный 500, отсутствие маркера/traceback и наличие безопасных type/code/request ID (`tests/test_readme_startup.py:253-288`).
- Startup error до Uvicorn записывается напрямую без `str(exc)`, `repr(exc)` и exception chaining (`src/skillhub/__main__.py:33-51`, `src/skillhub/__main__.py:71-79`); process-тест закрывает утечку неверного значения (`tests/test_readme_startup.py:209-230`).
- Structlog очищает `api_key`, `authorization`, `content`, `material`, `prompt`, `protocol`, `protocol_draft`, `secret`, `token` и чувствительные suffixes до renderer (`src/skillhub/core/logging.py:27-46`, `src/skillhub/core/logging.py:342-390`). Проверка дельты подтверждает вложенные `protocol`, authorization и пользовательский `material` (`tests/test_logging.py:13-42`).

## D. Injection и файлы

N/A для SQL/shell/YAML/upload/user path — таких потоков в E1-S1 нет. Пути шаблонов и статики строятся из фиксированного package path (`src/skillhub/app_factory.py:17-39`); шаблон не получает пользовательских значений (`src/skillhub/web/routes.py:41-52`).

## E. Криптография

N/A — токенов, паролей, шифрования и собственного key management нет. `uuid4().hex` служит только серверным correlation ID, не credential (`src/skillhub/web/security.py:51-56`).

## F. HTTP, Host, CORS и CSP

- Bind фиксирован на `127.0.0.1`, wildcard/external bind отсутствует (`src/skillhub/__main__.py:61-66`).
- TrustedHost допускает только `127.0.0.1`, `localhost`, `testserver`; его plain-text 400 заменяется на проектный JSON-envelope и безопасное событие (`src/skillhub/app_factory.py:29-34`, `src/skillhub/web/errors.py:167-218`).
- 404 и 405 переводятся в стабильные code/message; исходный path/Host не включается в ответ или лог (`src/skillhub/web/errors.py:20-26`, `src/skillhub/web/errors.py:103-134`).
- Дополнительный raw-socket прогон документированного процесса подтвердил application-level 400/404/405: `application/json`, ожидаемые коды и отдельный `X-Request-ID`; stdout/stderr не содержал traceback.
- CORS middleware отсутствует. Все ASGI-ответы получают CSP `default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`, `nosniff`, `no-referrer`, `DENY` и request ID (`src/skillhub/web/security.py:11-30`, `src/skillhub/web/security.py:51-59`).

## G. Replay, деньги и idempotency

N/A — платных вызовов, изменяющих операций и persistent state нет. Повторные health-запросы возвращают тот же контракт, но каждый получает новый server-generated request ID (`tests/test_web.py:38-62` в commit `cda20c3`).

## H. DoS и bounds

- Scrubber ограничивает один обход глубиной 16, 256 узлами и JSON-текстовым бюджетом 8192; исчерпание даёт детерминированный `[TRUNCATED]` (`src/skillhub/core/logging.py:14-19`, `src/skillhub/core/logging.py:49-70`, `src/skillhub/core/logging.py:87-108`).
- Контейнеры регистрируются по identity: цикл и повторная ссылка завершаются `[REFERENCE]`, а depth limit применяется до рекурсивного спуска (`src/skillhub/core/logging.py:111-133`, `src/skillhub/core/logging.py:138-208`, `src/skillhub/core/logging.py:235-259`).
- Диагностические поля идут первыми, поэтому event/error type/code/status/request ID не вытесняются широким payload (`src/skillhub/core/logging.py:261-280`).
- E1-S1 routes не читают request body и выполняют константную локальную работу. Явных application-level rate/header/body limits нет; фактическая поверхность ограничена localhost bind.

## I. XSS, client tokens и assets

- HTML содержит только статический текст и server-generated local asset URLs; пользовательские данные в DOM не вставляются (`src/skillhub/web/templates/home.html:1-23`, `src/skillhub/web/routes.py:41-52`).
- Клиентского JavaScript, browser storage и client token flow нет. Bootstrap и app CSS обслуживаются локально; CSP запрещает object/base/frame и внешние источники по умолчанию (`README.md:32-35`, `src/skillhub/web/security.py:11-12`).

## J. LLM: N/A — модельного data flow нет

В дельте нет клиента модели, prompt assembly, передачи `material`/`protocol` модели, tool calling, парсинга model output или исполнения содержимого скиллов. Поэтому prompt injection, output trust и полномочия LLM не имеют достижимого потока в E1-S1.

## Attack-test gaps

- В repository tests 400/404/405/Host envelope проверяется через TestClient (`tests/test_http_error_contract.py:11-65`), а spawned-process тесты покрывают access query и 500. Выполненный при этом gate raw-socket прогон не закреплён тестом.
- Malformed HTTP, отклонённый самим Uvicorn parser до входа в ASGI, возвращает его фиксированный `text/plain` 400 без request ID; raw probe подтвердил отсутствие traceback и входного содержимого в server log. Этот pre-ASGI путь отсутствует в repository tests.
- Bounds-тест утверждает размер результата, depth/nodes и graph references, но не измеряет CPU/peak-memory на многомегабайтной строке: `_bounded_text` сначала выполняет `json.dumps(value)` целиком (`src/skillhub/core/logging.py:87-108`).
- Нет adversarial-теста ключа mapping с custom `__str__`, произвольного `Mapping`/`set` и управляющих символов. Нестандартные значения сейчас сворачиваются в имя типа (`src/skillhub/core/logging.py:211-233`), а HTTP-поток E1-S1 их в логгер не передаёт.
- Git ignore закреплён для `.env`, `.env.*`, `*.key`, `*.pem`, agent/worktree paths, `*.log` и нового `*.tmp` (`.gitignore:1-50`; `tests/test_repository_contract.py:32-66`). История/entropy secret scan и private-key форматы вне перечисленных patterns отдельным attack-test не покрыты.

## Findings

P0: 0
P1: 0
P2: 0
P3: 0
