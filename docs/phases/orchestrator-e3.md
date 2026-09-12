# Оперативный план эпика E3 «Протокол встречи и Word»

Дата: 2026-09-12
Ветка: `e3-end`
База интеграции: `main` после E2 (`fee6040`)
Статус: принято

## Понимание эпика

- Пользователь проверяет готовый нормативный Markdown и скачивает `protocol.docx`.
- Ассистент выбирает `meeting-protocol` и собирает черновик через `create_draft`; сырой ответ модели не хранится.
- Страница `/protocol` в штатном `create_app` не вызывает модель: кнопка черновика выключена, выгрузка Word из готового текста работает.
- Не входит: таблица уточнений (E4), журнал расходов (E5).

## As-built

- SoT грамматики — `skills/meeting-protocol/references/protocol-format.md`.
- Четыре H2 обязательны в порядке Обсуждение → Решения → Задачи → Открытые вопросы. Опущенный H2 — `ProtocolParseError`.
- Пустой раздел — пункт `- —` или пустой tuple. `|` в полях задачи запрещён; при записи заменяется на `/`.
- Пакеты `protocol` и `docx_export` того же класса, что `registry`.
- HTTP: `GET /protocol`, `POST /protocol/draft`, `POST /protocol/docx`.
- DOCX только из `Protocol`; имя всегда `protocol.docx`.
- `docx_export` импортирует `skillhub.protocol.models`.
- `data-safety` и `migration` неприменимы: устойчивой записи встреч и DDL нет.

## Проверка эпика

- `ruff`, `mypy`, `import-linter`, `pytest` с порогом покрытия 85%.
