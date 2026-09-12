# Выжимка
Роль: security
Вердикт: GATE_OK
Находки: 0 — P0: 0, P1: 0, P2: 0, P3: 0

AppSec: ДА. LLM: НЕТ. Проверена только дельта `a471630..599bf5f`. `SEC-E1S1-R3-001` закрыт: верхнеуровневый обход события теперь ограничен 16 полями независимо от типа ключа; значения неизвестных полей заменяются фиксированным маркером без обхода или вызова пользовательских методов. Текущий allowlist диагностических полей получает только фиксированные коды, имена событий, типы внутренних исключений, числовые статусы, UTC-время и сгенерированный сервером request ID. Контейнеры и циклы не обходятся, а итоговый JSON сохраняет жёсткий предел 8192 байта. Регрессий запуска, HTTP-границ и журналирования в проверенном диапазоне не обнаружено.

# Отчёт
Рецепт: да

Проверены договор E1-S1 (`docs/phases/orchestrator-e1.md:42-96`), security rework-3 (`docs/handoff/e1-s1-rework-3-security.md:1-94`) и точная дельта `a471630..599bf5f`. В диапазоне изменены только `src/skillhub/core/_redaction.py` и `tests/test_logging.py`: 47 добавлений/130 удалений в production-коде и 80 добавлений/69 удалений в тестах. `git diff --check a471630..599bf5f` чист.

Проверка: `pytest -q` — 81 passed, coverage 96.53%; Ruff, format-check, mypy strict для `src tests` и import-linter прошли. Дополнительная runtime-проба без записи теста подтвердила `visited=16` для mapping со 100000 неподдерживаемых ключей, отсутствие вызовов `__str__`/`__repr__`/`__iter__` у hostile value и итоговый fallback размером 53 байта с newline.

## Фактический data flow

1. Документированный запуск `uv run python -m skillhub --port 8000` разбирает аргументы без отражения argv, ограничивает порт диапазоном `1..65535`, собирает приложение через безопасную process boundary и запускает Uvicorn только на `127.0.0.1` без access log (`README.md:7-18`, `src/skillhub/_server.py:29-52`, `src/skillhub/_server.py:84-120`).
2. Опубликованный `skillhub.main:app` использует ту же `create_process_app()` (`src/skillhub/main.py:1-5`). Ошибка конфигурации или сборки пишет отдельный фиксированный JSON через `_write_startup_failure()` без значения исключения и завершается `SystemExit(2) from None` (`src/skillhub/_server.py:55-97`).
3. После успешной конфигурации composition root включает structlog и безопасный Uvicorn formatter, затем собирает TrustedHost, security headers и внешнюю unexpected-error boundary (`src/skillhub/app_factory.py:21-46`).
4. Прикладное событие проходит `merge_contextvars` → добавление `level` → UTC `timestamp` → `redact_sensitive_fields` → `render_bounded_json` (`src/skillhub/core/logging.py:23-34`).
5. Redactor просматривает максимум 16 верхнеуровневых пар через `islice(event_dict.items(), MAX_FIELDS)`. Нестроковый ключ получает фиксированный `[UNSUPPORTED]`; строковый ключ ограничивается 512 символами. Только точное совпадение с allowlist может передать значение в `_redact_scalar`, любое иное поле немедленно получает `[REDACTED]` (`src/skillhub/core/_redaction.py:10-29`, `src/skillhub/core/_redaction.py:32-42`, `src/skillhub/core/_redaction.py:62-80`).
6. Диагностическое значение допускается только как точный built-in `str`, `bool`, `int`, `float` или `None`; подклассы, контейнеры, не-finite float и слишком широкий int становятся `[UNSUPPORTED]` без рекурсии (`src/skillhub/core/_redaction.py:45-59`).
7. Результат сериализуется с `ensure_ascii=True`; поэтому длина строки равна числу UTF-8-байтов. Если JSON вместе с newline превысил бы 8192 байта, возвращается фиксированный короткий fallback (`src/skillhub/core/_redaction.py:83-95`).
8. HTTP handlers передают в allowlist только фиксированные event/error codes, числовой status, внутренний exception class name и сгенерированный `uuid4().hex`; query, path, Host, body и текст исключения не передаются (`src/skillhub/web/_boundaries.py:21-55`, `src/skillhub/web/_boundaries.py:120-148`, `src/skillhub/web/errors.py:55-83`, `src/skillhub/web/security.py:33-59`).

## A. Вход и валидация

- Верхнеуровневый logging-контракт теперь deny-by-default: известен только allowlist `event`, `error_code`, `status_code`, `error_type`, `request_id`, `level`, `timestamp`; остальные значения всегда заменяются `[REDACTED]` (`src/skillhub/core/_redaction.py:15-29`, `src/skillhub/core/_redaction.py:68-78`).
- Для неизвестного поля условное выражение не вызывает `_redact_scalar(value)`, поэтому hostile value остаётся непрочитанной ссылкой. Committed tests подтверждают зачистку секретных, вложенных и будущих полей (`tests/test_logging.py:79-137`); runtime-проба дополнительно подтвердила ноль вызовов hostile methods.
- CLI и Settings в дельте не менялись; строгий startup-контракт по-прежнему покрывает неверные argv, неверное значение и неизвестную `SKILLHUB_*` переменную (`tests/test_readme_startup.py:419-536`).

## B. Authz, capabilities и tenant

N/A — E1-S1 содержит только локальные read-only root/health. Пользователей, tenant-данных, ролей, делегируемых полномочий и изменяющих операций нет. Сетевая граница рассмотрена в F.

## C. Секреты, пользовательский контент и логи

- Неизвестные значения больше не классифицируются по угадываемому списку чувствительных имён: они безусловно редактируются. Вложенные mappings/sequences и циклические значения не обходятся (`src/skillhub/core/_redaction.py:62-80`; `tests/test_logging.py:79-159`).
- Узкий allowlist не открывает достижимый поток пользовательского содержимого. `event` во всех production call sites — литерал; `error_code` — фиксированный framework/internal code; `status_code` — число; `request_id` — `uuid4().hex`; `level` и `timestamp` создаются processors; `error_type` — имя внутреннего класса исключения либо фиксированное `RuntimeError` (`src/skillhub/web/_boundaries.py:21-55`, `src/skillhub/web/_boundaries.py:140-148`, `src/skillhub/web/errors.py:55-83`, `src/skillhub/core/logging.py:23-34`).
- Startup и Uvicorn не полагаются на прикладной redactor: оба формируют собственные события только из фиксированных полей и не форматируют raw message, args или traceback (`src/skillhub/_server.py:55-97`, `src/skillhub/_server_logging.py:38-76`, `src/skillhub/_server_logging.py:80-112`).
- Process/HTTP проверки подтверждают отсутствие private query/config/exception markers, traceback и абсолютного пути (`tests/test_readme_startup.py:302-369`, `tests/test_readme_startup.py:460-536`, `tests/test_readme_startup.py:624-709`; `tests/test_web.py:135-211`).

## D. Injection и файлы

N/A для SQL, shell, YAML, upload и пользовательских путей — дельта не добавляет такие sinks. Redactor не исполняет, не парсит и не форматирует неизвестные значения; JSON строится стандартным encoder только из уже ограниченного plain dict безопасных scalars/markers (`src/skillhub/core/_redaction.py:62-95`).

## E. Криптография

N/A — credentials, токенов, шифрования и собственного key management в E1-S1 нет. `uuid4().hex` служит только correlation ID и не является credential (`src/skillhub/web/security.py:51-56`).

## F. HTTP, Host, CORS, CSP, lifecycle и unexpected boundary

- HTTP/startup-код в диапазоне не изменён. Поддерживаемый launcher остаётся на `127.0.0.1` с отключённым access log; TrustedHost разрешает только `127.0.0.1`, `localhost`, `testserver`; CORS middleware отсутствует (`src/skillhub/_server.py:100-108`, `src/skillhub/app_factory.py:31-38`).
- 400/404/405 сохраняют безопасную JSON-оболочку без path/Host, а ответы получают CSP, `nosniff`, `no-referrer`, `DENY` и server-generated request ID (`src/skillhub/web/errors.py:11-83`, `src/skillhub/web/security.py:11-59`; `tests/test_http_error_contract.py:10-65`).
- Unexpected failure до старта ответа остаётся санитизированным 500; late failure пишет коррелированное `http.response_aborted` и поднимает только фиксированный abort без исходного cause/context (`src/skillhub/web/_boundaries.py:107-148`; `tests/test_web.py:180-307`).
- Полный regression run, включая реальные subprocess startup/HTTP/logging проверки, прошёл.

## G. Деньги, replay и idempotency

N/A — платных вызовов, retries, изменяющих операций и persistent state нет. Повторные health-запросы остаются независимыми и получают разные server-generated request IDs (`tests/test_web.py:108-133`).

## H. DoS и строгие bounds

- `MAX_FIELDS=16` и `islice` ограничивают число извлечённых пар независимо от ширины mapping и до обработки неподдерживаемого ключа. После первых 16 пар `len(event_dict) > MAX_FIELDS` добавляет общий `[TRUNCATED]` (`src/skillhub/core/_redaction.py:10-10`, `src/skillhub/core/_redaction.py:62-80`).
- Runtime-проба на 100000 object-ключах посетила ровно 16 пар, сохранила `[UNSUPPORTED]` и `[TRUNCATED]` и не включила private value. Committed regression test проверяет bounded visits, оба marker и отсутствие приватного значения (`tests/test_logging.py:267-279`). `SEC-E1S1-R3-001` закрыт.
- Контейнеры, циклы, повторные ссылки и custom scalar subclasses не рекурсируются: неизвестные поля получают `[REDACTED]`, allowlisted неподдерживаемые значения — `[UNSUPPORTED]` (`tests/test_logging.py:140-207`, `tests/test_logging.py:253-264`).
- Каждая строка ограничена 512 символами; `ensure_ascii=True` и fallback обеспечивают жёсткий предел 8192 байта вместе с newline даже для максимальных Unicode code points (`src/skillhub/core/_redaction.py:32-38`, `src/skillhub/core/_redaction.py:83-95`; `tests/test_logging.py:210-226`).

## I. XSS, client tokens, assets, supply-chain и Git hygiene

- Дельта не меняет шаблон, client JavaScript, CSP, browser storage или token flow. Пользовательские значения в текущем UI отсутствуют.
- Runtime/dev зависимости, lock-файл и локальный Bootstrap не изменены; новых supply-chain входов нет.
- Изменения ограничены redactor и его тестами; `git diff --check` чист, архитектурный import contract сохранён.

## J. LLM: N/A — модельного data flow нет

LLM-scope: НЕТ. В диапазоне нет model client/gateway, prompt assembly, передачи пользовательского материала модели, tool calling, разбора model output или исполнения содержимого скиллов. Prompt injection и полномочия модели не имеют достижимого потока.

## Attack-test gaps

- Committed тест unsupported-key traversal проверяет `visited <= 256`, хотя production bound равен 16 (`tests/test_logging.py:267-279`). Runtime-проба подтвердила точное значение 16; полезно закрепить `<= MAX_FIELDS` в следующем допустимом тестовом изменении.
- Committed tests доказывают нулевой обход вложенных контейнеров для диагностических значений, но не инструментируют hostile methods неизвестного top-level value. Runtime-проба закрыла проверку для текущего review, однако отдельной committed регрессии нет.
- Имена неизвестных полей сохраняются в bounded-виде, а allowlist основан на имени поля. Текущие production call sites используют только schema-controlled keys и безопасные источники значений; будущему коду нельзя строить logging keys из пользовательского ввода или помещать пользовательский текст в allowlisted fields.
- Processor ожидает обычный structlog `EventDict`. Прямой вызов с dict subclass, переопределяющим `items()` или `__len__()` с побочными эффектами, не является достижимым production flow и отдельно не защищён.
- Raw-socket сценарии oversized headers/body, slow client и burst по-прежнему не покрыты. До-ASGI parser refusal остаётся ответственностью Uvicorn и не получает проектный request ID; компенсирующая граница E1-S1 — localhost bind.

## Findings

P0: 0
P1: 0
P2: 0
P3: 0
