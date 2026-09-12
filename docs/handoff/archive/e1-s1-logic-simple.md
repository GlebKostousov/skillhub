# Выжимка
Роль: logic-simple
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 3 — P1: 1, P2: 1, P3: 1.
Критичное расхождение: неизвестная переменная `SKILLHUB_*` молча игнорируется вместо fail-closed.
Голос HTTP не унифицирован: штатные 404/405 остаются англоязычными ответами FastAPI вне проектного envelope.
Root-экран дважды сообщает один статус и смешивает слова «приложение» и «сервис».
Полный quality gate зелёный: Ruff, format, mypy strict, import-linter, pytest (14 passed, 96.85%).
Поиск аналогов в `templates/static/shared` выполнен: в базовом коммите аналогов нет; в diff только первый root-шаблон и локальная статика.

# Отчёт
Рецепт: да

## Логика / простота

- [P1] [LS-001] [`src/skillhub/core/settings.py:16-21`] → `extra="forbid"` запрещает лишние поля при прямой валидации модели, но не неизвестные переменные операционной системы: runtime-проба с `SKILLHUB_UNEXPECTED=enabled` успешно вернула `environment='development'`. Это расходится с договором E1-S1 о fail-closed для лишней конфигурации → при старте явно проверять имена переменных с префиксом `SKILLHUB_` по разрешённому набору и завершать сборку при неизвестном имени.
- Остальная форма минимальна и соответствует границам слайса: `main.py` только публикует factory, `app_factory.py` собирает `core` и `web`, registry/LLM/SQLite и дополнительные ports не введены.
- Quality/import gate подтверждён фактическим запуском: Ruff, format, mypy strict, import-linter и pytest прошли; контракт `core` не импортирует web/composition roots сохранён.

## Голос проекта (канон: `docs/architecture/skillhub.md`, `docs/phases/orchestrator-e1.md`)

- [P2] [LS-002] [`src/skillhub/web/errors.py:71-78`] → централизованы только `SkillHubError` и неожиданные исключения, поэтому обычные 404/405 возвращаются как `{"detail":"Not Found"}` и `{"detail":"Method Not Allowed"}`. Это другой envelope, английский текст и не проектный словарь для ожидаемых HTTP-отказов → преобразовать framework HTTP-ошибки в стабильные машинные коды и ясные русские сообщения в принятом `{"error": ...}` формате, сохранив исходный HTTP-статус.
- [P3] [LS-003] [`src/skillhub/web/templates/home.html:16-18`] → «Локальное приложение запущено» и «Сервис работает» подряд дублируют один факт и называют один объект двумя терминами → оставить одно сообщение о состоянии и везде использовать каноническое «приложение».
- Типизированные ошибки используют короткие безопасные русские формулировки; внутренние event names остаются стабильными техническими идентификаторами.

## Дизайн-решения (канон: `docs/architecture/skillhub.md`, `docs/architecture/decision-rationale.md`)

- Jinja2, локальный Bootstrap 5.3.8 и минимальный обычный CSS совпадают с утверждённым каноном; внешних runtime-assets и нового бренда нет.
- CSP согласован с принятым значением, layout опирается на штатные Bootstrap container/card/badge utilities; отдельный frontend-toolchain или самописная design system не введены.
- Единственная правка композиции нужна по LS-003: убрать смысловой дубль, не добавляя новый визуальный паттерн.

## Компоненты

Поиск: `git ls-tree` базового `22bf7d9` по `templates/static/shared` не нашёл аналогов; текущий `src/skillhub/web` содержит только `home.html`, `app.css` и локальный Bootstrap asset. `rg` по card/container/badge/status подтвердил единственное место использования.

| блок | аналог | вердикт |
|---|---|---|
| Bootstrap container + card root-shell | В базовом коммите отсутствует | OK unique — первый серверный экран, собран из канонических Bootstrap utilities |
| Ссылка «К содержимому» | Bootstrap `.visually-hidden-focusable` | reuse — готовая локальная utility без собственного компонента |
| Readiness lead + success badge | В репозитории отсутствует | OK unique — единственный status-блок, но текстовую композицию исправить по LS-003 |
