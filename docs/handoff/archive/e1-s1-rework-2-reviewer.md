# Выжимка
Роль: reviewer
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 2 (P1: 1, P2: 1)
Повторная проверка: CLI boundary, различимые lifecycle diagnostics, единый composition root, единый 500-path, process adapter вне `core` и приватные модульные швы подтверждены. Не закрыты одинаковый startup-контракт обеих process-точек и отказ от boundary mini-framework.

# Отчёт
Рецепт: да

Проверенная дельта: `git diff cdf166e..96d417f`. `git diff --check`, Ruff, format-check, mypy strict, import-linter и pytest прошли (`72 passed`, coverage 95.81%). Дополнительно выполнены реальные process-пробы штатного старта, занятого порта и неверной конфигурации через опубликованный `skillhub.main:app`.

## A. Дефекты / риски

- [P1] [E1-S1-R2-REV-001] [README.md:13-18; src/skillhub/main.py:3-5; src/skillhub/app_factory.py:28-30] → заявленный одинаковый startup-контракт двух process-точек нарушен до настройки server adapter: `uvicorn skillhub.main:app` импортирует `main`, `create_app()` сначала создаёт `Settings()`, и только затем вызывает `configure_server_logging()`. Реальная проба с неверным `SKILLHUB_ENVIRONMENT` завершилась полным Python/Pydantic traceback без `startup.configuration_error`, `invalid_configuration` и машинного UTC-события. Документированный `python -m skillhub` тот же отказ обрабатывает корректно → провести import-time сборку опубликованного ASGI-объекта через ту же безопасную startup boundary, сохранив `app_factory.py` единственным composition root, а `main.py` — тонкой публикацией; неверное и неизвестное `SKILLHUB_*` в обеих process-точках должны давать один фиксированный event без traceback.
- [P2] [E1-S1-R2-REV-002] [src/skillhub/core/_redaction.py:10-51; src/skillhub/core/_redaction.py:53-217; src/skillhub/core/_redaction.py:220-249] → R2-SIZE формально разрезал прежний большой файл, но сам boundary mini-framework сохранился: отдельный 249-строчный модуль реализует собственную модель бюджета, графовую identity-семантику, depth/node/text limits, маркеры повторных ссылок, нормализацию произвольных ключей и второй bounded renderer. В allowlist уже включены поля будущих слайсов (`answer`, `clarification`, `intent`, `transcription`, `transcript`), которых в E1-S1 нет. Это перенос объёма из `core/logging.py`, а не сужение каркаса до текущих событий → зафиксировать малый JSON-compatible log-value contract E1-S1, отклонять неподдерживаемые значения на границе и оставить только необходимую рекурсивную зачистку/byte cap без собственной общей модели object graph и будущих payload-полей.

## B. System design

- Place: `_server.py` теперь владеет launcher, `_server_logging.py` — Uvicorn adapter вне `core`, HTTP mapping находится в `web/errors.py`, Host/process boundaries — в приватном `web/_boundaries.py`. Размещение выровнено, кроме недостижимости process adapter при раннем отказе `Settings()` в опубликованной точке.
- Contracts/errors: неверный CLI, неверная конфигурация документированной команды, bind failure и HTTP 500 имеют фиксированные коды. Process-проба занятого порта вернула `bind_failure` и корректный lifecycle shutdown. Расхождение второй process-точки описано в E1-S1-R2-REV-001.
- Idempotency: root/health не меняют состояние; request ID создаётся на запрос. Повторная сборка перенастраивает глобальные logging-конфигурации, но не создаёт прикладного состояния.
- SoT: полное ASGI-приложение собирает только `create_app()`, unexpected 500 формирует только `_unexpected_error_response()`, server log config создаёт только `build_server_log_config()`. Прежнее дублирование закрыто.
- Scale: сохранён один локальный процесс; workers, очереди, repositories и plugin framework не появились.
- Observability: реальный штатный запуск дал различимые `process_started`, `application_starting`, `application_started`, `listener_started` с UTC-временем; bind failure получил отдельный код. Ранний отказ опубликованного объекта остаётся вне этого контракта.

## C. Архитектура vs canon

`main.py` импортирует только `app_factory`, `__main__.py` — только `_server`; сборка middleware, routes и handlers сосредоточена в `create_app()`. Направление `main → app_factory`, единственный composition root, один источник 500 и отсутствие process-specific кода в `core.logging` восстановлены. Новые `_server*`, `core/_redaction.py` и `web/_boundaries.py` являются приватными швами; import-linter подтверждает независимость `core` от web/composition roots. При этом обобщённый redaction engine из E1-S1-R2-REV-002 всё ещё расходится с каноном «WMS-дисциплина без масштаба WMS» и требованием минимального `core`.

## D. Читаемость / понятность

Launcher, Uvicorn formatter, HTTP mapping и ASGI boundaries теперь читаются по отдельности; путь одного 500 прослеживается без сравнения двух реализаций. Основная оставшаяся когнитивная нагрузка — `_redaction.py`: для понимания обычного log event нужно учитывать общий бюджет, identity set, четыре лимита и взаимодействие двух processors.

Как это работает (просто): `python -m skillhub` безопасно разбирает порт, просит фабрику собрать приложение и запускает Uvicorn. Фабрика создаёт один FastAPI-объект с Host/request-ID/error boundaries. Все неожиданные HTTP-ошибки проходят через один безопасный 500, а Uvicorn пишет различимые фиксированные process-события без raw message. При прямой загрузке `skillhub.main:app` эта схема работает только после успешного чтения настроек; ошибка самих настроек происходит раньше и выпадает в обычный traceback.

## E. Чего нет / свежесть / повествование

В дельте нет новых runtime-зависимостей, lock-файла, реестра, LLM, SQLite, дополнительного процесса или прикладных сущностей следующих слайсов. Версии библиотек не менялись; текущий Uvicorn-путь проверен реальными стартом и bind failure. Повествование `fix: unify E1-S1 server boundaries` подтверждается для штатного launcher, 500-path, lifecycle-кодов и файловых швов, но не для раннего startup failure опубликованного объекта и не для заявленного удаления mini-framework.
