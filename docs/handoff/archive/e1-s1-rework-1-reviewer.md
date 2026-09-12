# Выжимка
Роль: reviewer
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 5 (P1: 2, P2: 3)
Повторная проверка: fail-closed для неизвестного и неверного `SKILLHUB_*` закрыт, но безопасное журналирование не стало единым свойством процесса, а rework разошёлся с каноническим composition root и минимальным размером E1-S1.

# Отчёт
Рецепт: да

Проверенная дельта: `git diff e8b11d7..cda20c3`.

## A. Дефекты / риски

- [P1] [E1-S1-R1-REV-001] [src/skillhub/main.py:3-6; src/skillhub/__main__.py:61-67] → безопасные настройки Uvicorn применяются только в новом launcher `python -m skillhub`, тогда как канонически опубликованный `skillhub.main:app` остаётся самостоятельной process-точкой и запускается с обычным access log; runtime-проба `uvicorn skillhub.main:app` записала полный target `GET /health?material=private-main-entry-query-4821` → выбрать одну публичную process-точку, выровнять её с каноном и сделать так, чтобы любой поддерживаемый запуск использовал один и тот же безопасный server log contract.
- [P1] [E1-S1-R1-REV-002] [src/skillhub/__main__.py:18-29,71-78] → `_parse_port()` выполняется до защищённого `try`, а стандартный `argparse` печатает исходное значение и немашинный usage; `--port private-runtime-content-84` и неизвестный аргумент раскрываются в stderr дословно → включить разбор CLI в startup boundary и заменить стандартный error-path фиксированным машинным событием без исходных аргументов.
- [P2] [E1-S1-R1-REV-003] [src/skillhub/core/logging.py:283-308] → `SafeServerFormatter` сохраняет безопасность ценой потери смысла: разные стадии старта и остановки становятся одинаковыми `server.lifecycle`, warning отличается только уровнем, а bind failure без `exc_info` становится общим `ServerError`; реальный старт выдал четыре неразличимых события, занятый порт — общий код без причины и без времени → определить небольшой allowlist стабильных process-событий с UTC-временем и диагностическим кодом, не возвращая raw message или traceback.
- [P2] [E1-S1-R1-REV-004] [src/skillhub/app_factory.py:22-42; src/skillhub/main.py:3-6; src/skillhub/__main__.py:11-13,54-67; src/skillhub/web/errors.py:137-165,221-277] → сборкой теперь владеют сразу `app_factory`, `main` и `__main__`: два entrypoint отдельно добавляют `ProcessErrorBoundary`, а формирование и логирование 500 продублировано в handler и внешней границе; `app_factory.py` перестал быть единственным composition root, `main.py` перестал только публиковать собранное приложение, а error contract имеет два источника истины → оставить одну функцию сборки полного ASGI-приложения и один владелец 500-path, entrypoint должны только делегировать.
- [P2] [E1-S1-R1-REV-005] [src/skillhub/core/logging.py:49-365; src/skillhub/core/settings.py:15-84; src/skillhub/web/errors.py:26-277] → исправление превратило стартовый каркас в собственный boundary mini-framework: `core/logging.py` вырос до 406 строк и совмещает обход произвольного object graph, лимиты сериализации и Uvicorn-конфигурацию; `web/errors.py` вырос до 287 строк и совмещает envelope, framework mapping, перехват TrustedHost-ответа и process boundary; четыре ключевых файла rework получили 690 строк при одном поле настроек и двух маршрутах → сузить поддерживаемый log-value contract, отделить process adapter от общего `core`, разделить HTTP mapping, host policy и process boundary по утверждённым приватным швам, убрав дублирование вместо наращивания универсального механизма.

## B. System design

- Place: строгая env-валидация логично остаётся в `core.settings`, а HTTP statuses/envelope — в `web`; Uvicorn logger config является process adapter, но сейчас опубликован через общий фасад `core`.
- Contracts/errors: неизвестный и неверный `SKILLHUB_*` действительно завершают сборку кодом 2 с безопасным `startup.configuration_error`; 400/404/405 сохраняют исходные статусы и единый envelope, expected 422 и unexpected 500 сохраняют стабильные коды. Process contract нарушают E1-S1-R1-REV-001/002.
- Idempotency: `/health` остаётся чистым чтением, request ID создаётся на запрос, прикладного изменяемого состояния rework не добавил. Глобальная настройка structlog повторяется при каждой `create_app()`, как и до rework.
- SoT: `_FRAMEWORK_ERRORS` централизует framework statuses, но unexpected 500 и полная process-сборка имеют по две реализации.
- Scale: модель одного локального процесса сохранена; очереди, workers и распределённые механизмы не введены.
- Observability: request ID неожиданной ошибки теперь совпадает с заголовком ответа, поля `protocol` и другие канонические payload-поля зачищаются; process lifecycle и startup diagnostics деградируют до неразличимых событий по E1-S1-R1-REV-003.

## C. Архитектура vs canon

Import-linter подтверждает только узкий запрет `core → web/app_factory/main`, и он соблюдён. Более полный канон `main → app_factory`, «`app_factory.py` — единственный composition root» и «`main.py` только публикует приложение» нарушен прямыми импортами `main/__main__ → web` и повторной обёрткой приложения. Семантическая зависимость `core.logging` от имён логгеров Uvicorn также переносит process-specific знание в общий модуль. Реестр, LLM, SQLite, repositories, plugin execution и сущности следующих слайсов не добавлены, но инфраструктурный mini-framework уже превышает границы минимального E1-S1.

## D. Читаемость / понятность

Имена кодов ошибок, env allowlist и request-ID path читаются однозначно. Понимание целого, однако, требует одновременно держать в голове FastAPI `ServerErrorMiddleware`, внутренний exception handler, внешний `ProcessErrorBoundary`, два entrypoint и отдельный Uvicorn formatter. Большие файлы скрывают короткий пользовательский сценарий; `_StrictEnvironmentSource` дополнительно зависит от защищённых методов Pydantic Settings `_apply_case_sensitive` и `_extract_field_info`.

Как это работает (просто): фабрика создаёт FastAPI с маршрутами, защитными заголовками и HTTP-ошибками. Новый CLI отдельно проверяет порт, создаёт приложение, ещё раз оборачивает его process-границей и запускает Uvicorn с отключённым access log. Неизвестные `SKILLHUB_*` отклоняются, чувствительные поля структурированных событий заменяются маркерами, а неожиданный HTTP-отказ получает безопасный JSON и request ID. Расхождение возникает потому, что второй опубликованный способ запуска не получает те же настройки, а общий formatter удаляет почти всю диагностическую семантику.

## E. Чего нет / свежесть / повествование

В дельте нет новых runtime-зависимостей, изменений lock-файла, внешнего bind, CORS wildcard, небезопасного env fallback или прикладных сущностей E1-S2; `git diff --check` чист, import-контракт `core` сохранён. Версии библиотек не менялись, поэтому dependency-freshness остаётся на уровне исходного E1-S1; при этом использование защищённых API `pydantic-settings` делает будущую совместимость менее устойчивой. Повествование коммита «close E1-S1 rework findings» подтверждается для env и штатного happy-path нового launcher, но не подтверждается для полного process boundary, наблюдаемости и утверждённой архитектуры.
