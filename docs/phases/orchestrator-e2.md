# Оперативный план эпика E2 «Автовыбор режима и обычные ответы»

Дата: 2026-09-12
Ветка: `orch/e2`
База: `7c71df955330a843ac437f22cf9c0bf1cf0a6cfa`
Run: `e2`
Статус: принято

## Понимание эпика

- Пользователь формулирует намерение и добавляет материал. Классификатор выбирает один из пяти режимов или `none`. Четыре простых режима возвращают текстовый ответ; выбранный режим виден.
- Поток: изолированный шлюз модели → классификатор только по намерению → общий текстовый обработчик → окно ассистента и матрица.
- Параллельный эпик E3 стартует с той же базы и владеет протоколом, Word и `meeting-protocol` handler. E2 не создаёт эти пакеты и не эмулирует Word.
- Слайсы зависимы: `E2-S1 → E2-S2 → E2-S3 → E2-S4`.
- Security-scope: **ДА** — ключ поставщика, недоверенные intent/material, CSRF JSON, логи без content, SSRF через URL модели.
- LLM-scope: **ДА** — classify и generate без инструментов; ответ модели недоверенный.

## Было и должно стать

- Было: приложение стартует без ключа, показывает каталог пяти скиллов, не вызывает модель и не принимает намерение.
- Должно стать: безопасный шлюз DeepSeek, автовыбор режима, рабочие текстовые ответы четырёх простых скиллов, видимый бейдж выбора, матрица не ниже 95%. Выбор `meeting-protocol` показывает режим и честный исход `handler_unavailable`, пока E3 не зарегистрирует обработчик.

## Архитектурное покрытие

Architect: `357baf5a-5ded-49aa-9a4c-22cd125abfe8`.

- Новые классы module: `llm`, `classifier`, `assistant`. Образец фасада — `skillhub.registry`.
- E2 не создаёт `protocol`, `docx_export`, `usage`.
- Направление: `main → app_factory → web → assistant → classifier → llm|registry → core`.
- Settings и префикс `SKILLHUB_` не расширяются. Ключ читает только пакет `llm` из переменной окружения поставщика, без файла окружения.
- Каталожный `create_router()` не расширяется. Ассистент живёт в `create_assistant_router()`.
- Shared-файлы правит только короткий sequential merge; одновременно один эпик.

### Решения

| ID | Решение |
|---|---|
| D-E2-01 | Три новых модуля; protocol/docx/usage запрещены. |
| D-E2-02 | `meeting-protocol` может быть выбран; исход `handler_unavailable`, материал в модель не идёт. |
| D-E2-03 | Конфигурация `SKILLHUB_*` без новых полей; URL и лимиты — константы `llm`. |
| D-E2-04 | Новый assistant router; `httpx` как runtime; import-linter без несуществующих пакетов. |
| D-E2-05 | Claims E2 и резерв E3 разделены; `skills/meeting-protocol/` E2 не пишет. |
| D-E2-06 | Контракты gateway, `POST /api/assistant`, исходы `success/none/handler_unavailable/generation_unavailable/provider_error`. |
| D-E2-07 | `FakeLlmGateway` — публичный тестовый шов фасада `llm`. |
| D-E2-08 | assistant не импортирует protocol/docx; classifier не видит material и body. |

## Зоны владения при параллельном E3

E2 пишет только свои префиксы. E3 резервирует protocol, docx, UI протокола и `skills/meeting-protocol/references/`.

Shared sequential, не epic-long claim: `app_factory.py`, `web/__init__.py`, `home.html`, `base.html`, `pyproject.toml`, `uv.lock`, `.importlinter`, `tests/test_web.py`, as-built docs.

E2 не меняет: `web/routes.py`, `core/settings.py`, `web/errors.py`, `web/_reload_guard.py`.

## Баланс

Формула E1: критерий +1, новый модуль +1, ветка поведения +1, смена публичного контракта +2.

| Слайс | Критерии | Модули | Ветки | Контракт | Итого |
|---|---:|---:|---:|---:|---|
| E2-S1 | 5 | 1 | 4 | 2 | 12 |
| E2-S2 | 5 | 1 | 3 | 2 | 11 |
| E2-S3 | 5 | 1 | 3 | 2 | 11 |
| E2-S4 | 5 | 2 | 3 | 2 | 12 |

## Слайсы

1. `E2-S1` — изолированный шлюз модели.
2. `E2-S2` — классификатор.
3. `E2-S3` — общий обработчик простых режимов.
4. `E2-S4` — окно ассистента и classifier matrix.

## Проверка эпика

- `ruff`, `mypy`, `import-linter`, `pytest` с порогом покрытия 85%.
- Атакующие проверки ключа, URL, логов и CSRF.
- Матрица классификатора не ниже 95%, неизвестных имён нет.

## Правило движения

Один writer в orchestration worktree. E3 использует собственную ветку и reserved claims. Push в `main` только по явной команде владельца.
