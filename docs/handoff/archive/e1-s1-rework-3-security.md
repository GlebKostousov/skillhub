# Выжимка
Роль: security
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 1 — P0: 0, P1: 0, P2: 0, P3: 1

AppSec: ДА. LLM: НЕТ. `SEC-E1S1-R2-001` и `SEC-E1S1-R2-002` закрыты: стандартный импорт `skillhub.main:app` безопасно и одинаково завершает неверную и неизвестную конфигурацию, а ошибка после начала ответа пишет коррелированное санитизированное событие и передаёт наружу только фиксированный abort. Предыдущие меры для секретов, логов, HTTP, Host, конфигурации и итогового byte cap сохранены. Gate всё ещё блокирует новый P3: redactor не расходует node budget на неподдерживаемые ключи словаря и может линейно обойти произвольно широкий mapping.

# Отчёт
Рецепт: да

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), security rework-2 (`docs/handoff/e1-s1-rework-2-security.md:1-97`) и точная одно-коммитная дельта `b7ea37c..66c20b1`. Полный набор commit-range прошёл: `pytest -q` — 78 passed, coverage 96.98%; Ruff, format-check, mypy strict и import-linter прошли; `git diff --check b7ea37c..66c20b1` чист. Ограниченный поиск credential/private-key signatures в tracked tree и истории совпадений не нашёл. Дополнительная runtime-проба production-redactor на 100000 неподдерживаемых ключах дала `visited=100000; private_in_result=False; truncated=False`, подтвердив finding ниже.

## Фактический data flow

1. Документированная команда `uv run python -m skillhub --port 8000` идёт через `skillhub.__main__` в `_server.main()` (`README.md:7-18`, `src/skillhub/__main__.py:1-8`). Аргументы разбираются без отражения argv, порт ограничен `1..65535`, после чего приложение создаётся через общую process boundary и Uvicorn запускается только на `127.0.0.1` без access log (`src/skillhub/_server.py:29-52`, `src/skillhub/_server.py:84-120`).
2. Стандартный Uvicorn импортирует `skillhub.main:app`, а `main.py` теперь вызывает ту же `create_process_app()` (`src/skillhub/main.py:1-5`). `Settings()` либо проходит, либо поднимает `ValidationError`; process boundary пишет один фиксированный JSON без значения исключения и завершает импорт `SystemExit(2) from None` (`src/skillhub/app_factory.py:21-30`, `src/skillhub/_server.py:55-97`).
3. Строгий env source передаёт неизвестные `SKILLHUB_*` как extra в Pydantic, `extra="forbid"` закрывает запуск, `.env` отключён, а `hide_input_in_errors=True` не отражает исходное значение (`src/skillhub/core/settings.py:15-38`, `src/skillhub/core/settings.py:40-84`). Spawned-Uvicorn тест покрывает и неверный `SKILLHUB_ENVIRONMENT`, и неизвестное имя, проверяя один safe event без marker, traceback и абсолютного пути (`tests/test_readme_startup.py:493-536`).
4. После успешной конфигурации composition root включает structlog и безопасную Uvicorn-конфигурацию, затем собирает внешний `ProcessErrorBoundary`, request-ID/security-headers слой и внутренний TrustedHost (`src/skillhub/app_factory.py:21-46`). Uvicorn formatter не форматирует raw message, args или traceback, а access logger лишён handlers (`src/skillhub/_server_logging.py:38-76`, `src/skillhub/_server_logging.py:80-112`).
5. Для HTTP `SecurityHeadersMiddleware` генерирует `uuid4().hex`, сохраняет его в scope state/contextvars и добавляет CSP, `nosniff`, `no-referrer`, `DENY` и `X-Request-ID` (`src/skillhub/web/security.py:11-30`, `src/skillhub/web/security.py:33-59`). Host/framework/expected paths логируют только фиксированные event/code/status и возвращают безопасные JSON-envelope (`src/skillhub/web/_boundaries.py:58-104`, `src/skillhub/web/errors.py:11-93`).
6. Unexpected exception до `http.response.start` превращается в санитизированный 500 с тем же request ID (`src/skillhub/web/_boundaries.py:21-55`, `src/skillhub/web/_boundaries.py:107-139`). После старта ответа boundary извлекает тот же ID, пишет `http.response_aborted`/`response_aborted`, затем вне активного `except` поднимает только фиксированный `RuntimeError` без cause/context и исходного сообщения (`src/skillhub/web/_boundaries.py:120-148`). Реальный streaming-тест получает незавершённое тело, тот же ID в safe event и санитизированный Uvicorn `server_failure` (`tests/test_readme_startup.py:673-709`).
7. Прикладные события проходят через context merge, фиксированные level/time, рекурсивную зачистку и bounded JSON renderer (`src/skillhub/core/logging.py:17-35`). Redactor допускает только dict/list/tuple и JSON-совместимые bounded scalars, зачищает актуальные секретные/контентные поля и суффиксы, ограничивает depth/text/output (`src/skillhub/core/_redaction.py:10-68`, `src/skillhub/core/_redaction.py:82-179`). Ветка неподдерживаемого ключа, однако, не расходует node budget.

## A. Вход и валидация

- CLI не отражает неверное значение/неизвестный аргумент и принимает только порт `1..65535` (`src/skillhub/_server.py:29-52`; `tests/test_readme_startup.py:419-457`, `tests/test_readme_startup.py:558-581`).
- Конфигурация допускает только `development|test|production`, не читает `.env`, запрещает неизвестные `SKILLHUB_*` и скрывает input в validation error (`src/skillhub/core/settings.py:12-55`).
- `SEC-E1S1-R2-001` закрыт: обе process-точки используют `create_process_app()`, а invalid/unknown env через стандартный Uvicorn подтверждены spawned-process тестом (`src/skillhub/_server.py:84-97`, `src/skillhub/main.py:1-5`, `tests/test_readme_startup.py:493-536`).

## B. Authz, capabilities и tenant

N/A — E1-S1 содержит только локальные read-only root/health, без пользователей, tenant-данных, ролей, делегируемых полномочий и изменяющих операций. Сетевая граница рассмотрена в F.

## C. Секреты, пользовательский контент и логи

- Startup boundary сериализует только event/code/type/time и не использует `str(exc)`, `repr(exc)` или chaining (`src/skillhub/_server.py:55-97`).
- Server formatter игнорирует raw message/args/traceback, различает lifecycle/bind/generic failure и отключает access log (`src/skillhub/_server_logging.py:9-18`, `src/skillhub/_server_logging.py:38-112`).
- Прикладной scrubber зачищает `api_key`, `authorization`, `content`, `material`, `password`, `protocol`, `secret`, `token` и secret suffixes; неподдерживаемые значения/ключи не сериализуются (`src/skillhub/core/_redaction.py:27-45`, `src/skillhub/core/_redaction.py:82-147`; `tests/test_logging.py:50-137`).
- Текущие HTTP call sites не передают query/path/Host/body в логи; error events содержат только фиксированные поля и server-generated request ID (`src/skillhub/web/_boundaries.py:21-55`, `src/skillhub/web/errors.py:55-83`). Process-тесты подтверждают отсутствие query/exception markers (`tests/test_readme_startup.py:302-369`, `tests/test_readme_startup.py:624-709`).
- Ограниченный secret scan дерева и истории совпадений не нашёл; `.env`, `.env.*`, `*.key`, `*.pem` и логи игнорируются (`.gitignore:1-5`, `.gitignore:42-50`).

## D. Injection и файлы

N/A для SQL, shell, YAML, upload и пользовательских путей — таких входов в E1-S1 нет. Пути шаблонов/статики строятся из фиксированного package path, а root передаёт шаблону только объект request (`src/skillhub/app_factory.py:18-43`, `src/skillhub/web/routes.py:30-53`).

## E. Криптография

N/A — credentials, токенов, шифрования и собственного key management в слайсе нет. `uuid4().hex` используется только как correlation ID, не как credential (`src/skillhub/web/security.py:51-56`).

## F. HTTP, Host, CORS, CSP, lifecycle и unexpected boundary

- Поддерживаемая команда фиксирует `127.0.0.1`, отключает access log и передаёт safe log config (`src/skillhub/_server.py:100-108`). Для опубликованного объекта README явно оставляет сетевые параметры команде Uvicorn (`README.md:13-18`).
- TrustedHost допускает только `127.0.0.1`, `localhost`, `testserver`; CORS middleware отсутствует; 400/404/405 получают стабильный JSON без path/Host и CORS-заголовков (`src/skillhub/app_factory.py:31-38`, `src/skillhub/web/_boundaries.py:58-104`, `src/skillhub/web/errors.py:11-83`; `tests/test_http_error_contract.py:10-65`).
- Все прикладные ответы получают CSP, `nosniff`, `no-referrer`, `DENY` и server-generated request ID (`src/skillhub/web/security.py:11-59`; `tests/test_web.py:169-178`).
- `SEC-E1S1-R2-002` закрыт: late failure больше не поглощается, а даёт коррелированное bounded event и санитизированный abort; unit ASGI и spawned-Uvicorn проверки исключают private marker/traceback и подтверждают незавершённое тело (`src/skillhub/web/_boundaries.py:120-148`; `tests/test_web.py:244-308`, `tests/test_readme_startup.py:673-709`).
- Ошибки HTTP parser до входа в ASGI остаются Uvicorn boundary и не получают проектный envelope/request ID.

## G. Деньги, replay и idempotency

N/A — платных вызовов, retries, изменяющих операций и persistent state нет. Повторные health-запросы возвращают один контракт с независимыми server-generated request ID (`tests/test_web.py:108-133`).

## H. DoS и строгие bounds

- Redactor ограничивает глубину 16, поддерживаемые узлы 256, консервативный текстовый бюджет 6000 и итоговый ASCII JSON вместе с newline 8192 байтами (`src/skillhub/core/_redaction.py:10-25`, `src/skillhub/core/_redaction.py:47-59`, `src/skillhub/core/_redaction.py:150-179`). Существующие проверки подтверждают depth, sequence width, long text, peak allocation и output cap (`tests/test_logging.py:140-201`).
- Для строковых ключей и sequence items node budget останавливает обход. Для неподдерживаемого ключа код сразу пишет `[UNSUPPORTED]` и делает `continue`, не уменьшая `nodes_left`; генератор `_mapping_items()` поэтому перечисляет весь mapping (`src/skillhub/core/_redaction.py:71-79`, `src/skillhub/core/_redaction.py:113-130`). Runtime-проба обошла все 100000 элементов без утечки значения, но без `[TRUNCATED]`: `SEC-E1S1-R3-001`.
- Root/health не читают body и выполняют константную локальную работу (`src/skillhub/web/routes.py:30-53`). Application-level rate, connection, header и body limits отсутствуют; основной compensating control — localhost bind.

## I. XSS, client tokens, assets, supply-chain и Git hygiene

- Шаблон содержит только статический текст и локальные asset URLs; пользовательских значений, JavaScript, browser storage и client token flow нет (`src/skillhub/web/templates/home.html:1-22`).
- Bootstrap обслуживается локально; версия и SHA-256 закреплены README (`README.md:38-43`). `pyproject.toml` и `uv.lock` в дельте не менялись, новых runtime-зависимостей нет.
- Основные secret/runtime/worktree артефакты игнорируются, tracked index-тексты имеют `i/lf`, repository-contract покрывает ignore/EOL/local config и core boundary (`.gitignore:1-50`, `tests/test_repository_contract.py:33-181`).

## J. LLM: N/A — модельного data flow нет

LLM-scope: НЕТ. В дельте отсутствуют model client/gateway, prompt assembly, передача `material`/`protocol` модели, tool calling, парсинг model output и исполнение содержимого скиллов. Prompt injection и полномочия модели не имеют достижимого потока.

## Attack-test gaps

- В commit-range нет проверки широкого mapping, состоящего из неподдерживаемых ключей; существующий traversal test покрывает только sequence (`tests/test_logging.py:190-201`). Дополнительная runtime-проба воспроизводит finding `SEC-E1S1-R3-001`.
- Нет тестов custom dict/list/str/int subclasses, side-effectful equality/iteration, конфликтующих либо обрезанных ключей и управляющих символов. Текущие production call sites такие объекты не принимают.
- Нет spawned-Uvicorn generic build-failure теста именно для `skillhub.main:app`: invalid/unknown env там покрыты, а произвольный build failure проверен только через публичную функцию launcher (`tests/test_readme_startup.py:493-605`).
- Нет spawned-process unexpected/late тестов именно для опубликованного entry; process-пробы собирают тот же factory и запускают его через `_server.run_server()` (`tests/test_readme_startup.py:120-186`, `tests/test_readme_startup.py:624-709`).
- Нет raw-socket regression tests для malformed HTTP, oversized headers/body, slow client и burst. До-ASGI parser refusal не получает проектный request ID.
- Git ignore не является secret boundary: `*.p12`, `*.pfx` и `id_rsa` текущими patterns не закрыты. Выполненный поиск сигнатур ограничен и не заменяет entropy/full credential scan.

## Findings

P0: 0
P1: 0
P2: 0

- [P3] [SEC-E1S1-R3-001] [src/skillhub/core/_redaction.py:71-79, src/skillhub/core/_redaction.py:113-130, tests/test_logging.py:190-201] Неподдерживаемые ключи mapping не расходуют node budget: ветка записывает фиксированный marker и продолжает обход, поэтому mapping из 100000 object-ключей посещён целиком (`visited=100000`) и не получил `[TRUNCATED]`. Значения не раскрылись, а текущие E1-S1 HTTP call sites не передают произвольные mappings в logger, поэтому приоритет P3; однако заявленный независимый от ширины CPU-bound нарушен и будущий лог недоверенного parsed payload сможет блокировать event loop/worker пропорционально числу ключей. → Ограничивать число просмотренных mapping entries до/включая ветку неподдерживаемого ключа (сохранив diagnostic-first и redaction), затем закрепить committed regression test с counting mapping и проверками bounded visits, marker и отсутствия приватного значения.
