# Выжимка
Роль: security
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 3 — P1: 1, P2: 2

# Отчёт
Рецепт: да
AppSec scope: HTTP, конфигурация, секреты, логирование, CSP, TrustedHost, зависимости, Git ignore/EOL/local config.
Проверенный diff: `git diff 22bf7d9..ce9390f`, слайс E1-S1.

## A. Вход и валидация

- HTTP-вход ограничен фиксированными `GET /` и `GET /health`; пользовательские тела и параметры не обрабатываются.
- `SKILLHUB_ENVIRONMENT` типизирован и недопустимое значение останавливает сборку приложения.
- Неизвестные `SKILLHUB_*` переменные окружения фактически игнорируются: `SKILLHUB_UNEXPECTED=enabled` приводит к успешному `Settings()` со значением `development`. Это finding `SEC-E1S1-002`.

## B. Authz, capabilities, tenant

N/A — E1-S1 является локальным однопользовательским read-only каркасом: изменяющих маршрутов, tenant-данных, ролей и делегируемых полномочий нет. Сетевая граница рассмотрена в блоке F.

## C. Секреты и логи

- `.env`, `.env.*`, `*.key`, `*.pem` и логи исключены из Git; проверка tracked runtime-файлов не обнаружила ключей или private-key блоков.
- `structlog` рекурсивно зачищает известные чувствительные ключи и суффиксы; текущие прикладные события передают только фиксированные code/status/type.
- Catch-all HTTP handler возвращает безопасное тело, но Starlette повторно поднимает exception до Uvicorn. Реальный сервер вывел полный traceback и маркер `private-runtime-content-84`: finding `SEC-E1S1-001`.
- При ошибке `SKILLHUB_ENVIRONMENT` процесс до настройки structlog печатает исходное `input_value` в traceback Uvicorn: finding `SEC-E1S1-003`.

## D. Injection и файлы

N/A — SQL, shell, YAML, загрузок и пользовательских путей в E1-S1 нет. Каталоги шаблонов и статики вычисляются из фиксированного пути пакета; содержимое страницы статично.

## E. Криптография

N/A — криптографических протоколов, токенов, паролей и собственного управления ключами в diff нет. `uuid4().hex` используется только как непредсказуемый request ID, не как credential.

## F. HTTP, Host, CORS, CSP и детали ошибок

- Документированная команда и стандартный Uvicorn bind используют `127.0.0.1`; внешнего bind и wildcard-host нет.
- `TrustedHostMiddleware` допускает только `127.0.0.1`, `localhost`, `testserver`; запрос с `Host: attacker.example` получает 400.
- CORS middleware отсутствует; 404 с чужим `Origin` не получает `Access-Control-Allow-Origin` и не отражает путь.
- Все проверенные ответы получают CSP `default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`, `nosniff`, `no-referrer`, `DENY` и серверный request ID.
- Типизированная ошибка возвращает только стабильные `code` и `public_message`; неожиданная ошибка не раскрывается в HTTP, но её содержимое раскрывается серверным логом — `SEC-E1S1-001`.

## G. Деньги, replay и idempotency

N/A — платных вызовов, изменяющих операций и persistent state нет. Два последовательных старта и повторные health-запросы вернули одинаковый `200 {"status":"ok"}` с отдельными request ID.

## H. Лимиты и DoS

- Текущие маршруты не читают тело запроса и выполняют константную локальную работу.
- Явных лимитов заголовков, соединений и частоты запросов в приложении нет; проверка этих свойств должна выполняться на реальном Uvicorn-процессе, а не только через TestClient.

## I. XSS, client tokens, assets и supply-chain

- Шаблон не вставляет недоверенные значения; клиентского JavaScript, browser storage и клиентских токенов нет.
- Bootstrap 5.3.8 поставляется локально. Фактический SHA-256 `D85327D99C7A3EE1F9B5D0500D1370ACEA3AD2DB39C163C2F51F232BAEDBDEDE` совпал с README.
- `uv.lock` согласован с `pyproject.toml`: FastAPI 0.141.1, Starlette 1.6.0, Jinja2 3.1.6, Pydantic Settings 2.15.0, Pydantic 2.13.5, Structlog 26.1.0, Uvicorn 0.52.4, H11 0.16.0, HTTPX2/HTTPCore2 2.12.0 и AnyIO 4.10.0. Все 42 registry-релиза загружаются с PyPI и имеют SHA-256 для артефактов; проверка metadata advisories для точных locked-версий не вернула известных записей.
- `git diff --check` чист; index-тексты нормализованы в LF. Repo-local config содержит `core.autocrlf=false`, `pull.ff=only`, `fetch.prune=true`, `push.autoSetupRemote=true`. Проверенные `.env`, key, log, venv и worktree paths корректно игнорируются.

## J. LLM: N/A — модельных вызовов нет

В E1-S1 отсутствуют LLM gateway, prompt/material flow, модельный ответ, tool calling и исполнение содержимого скиллов. Поэтому prompt injection, output parsing и модельные полномочия не имеют фактического data flow в этом diff.

## Attack-test gaps

- Нет spawned-Uvicorn теста, который захватывает stdout/stderr и ищет секретный маркер в полном server log; TestClient + `capture_logs()` не видит traceback Uvicorn.
- Нет теста неизвестной prefixed env-переменной (`SKILLHUB_UNEXPECTED`) и опечатки в имени security-настройки.
- Нет process-level теста, что ValidationError конфигурации не печатает исходное значение.
- Нет автоматического стража SHA-256 vendored asset, проверки lock/advisories и secret scan истории.
- Git ignore/EOL/repo-local config проверены командами, но не закреплены repository-contract тестом в reviewed commit.
- Нет real-socket атакующих проверок oversized headers/body, медленного клиента и burst запросов.
- Redactor не атакован произвольными именами полей, event string, set/custom object и управляющими символами.

## Findings

- [P1] [SEC-E1S1-001] [src/skillhub/web/errors.py:42-77 / catch-all возвращает 500, после чего Uvicorn пишет исходный exception; воспроизведено `RuntimeError: private-runtime-content-84`] → Starlette `ServerErrorMiddleware` повторно поднимает неожиданную ошибку после ответа, поэтому пользовательский материал или секрет из exception message попадает в серверный traceback, хотя TestClient-проверка показывает безопасный результат → поставить процессный ASGI error boundary/безопасную конфигурацию server error logging, не передающую исходный exception в Uvicorn, и добавить spawned-Uvicorn тест с секретным маркером.
- [P2] [SEC-E1S1-002] [src/skillhub/core/settings.py:16-23 / `SKILLHUB_UNEXPECTED=enabled` молча проигнорирован] → `extra="forbid"` запрещает лишние model input, но не неизвестные env keys; опечатка в prefixed security-конфигурации запускает приложение с default и нарушает fail-closed договор → валидировать полный allowlist переменных с префиксом `SKILLHUB_` до сборки Settings и тестировать неизвестную prefixed env-переменную через `Settings()` и `create_app()`.
- [P2] [SEC-E1S1-003] [src/skillhub/app_factory.py:26 / `SKILLHUB_ENVIRONMENT=super-secret-config-value` попал в startup traceback как `input_value`] → валидация выполняется до `configure_logging`, а Uvicorn печатает исходный `ValidationError`; ошибочно помещённый секрет или чувствительное локальное значение оказывается в process logs → скрыть input в validation errors и преобразовывать startup config failure в санитизированную ошибку без exception chaining; подтвердить spawned-process тестом stdout/stderr.
