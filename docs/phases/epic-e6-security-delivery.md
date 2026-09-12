# E6. Защита и поставка

Статус: принят. As-built: `tests/security/`, `eval/run_eval.py --offline`, CI, Docker editable+src, `eval/run_demo.py --offline`.
Вес: 8 единиц
Зависит от: E1–E5

## Результат

Проект воспроизводимо запускается, проходит полный quality/security/eval gate и готов к демонстрации. Защита показывает конкретные границы доверия и измеримые результаты, а не обещание «модель невозможно атаковать».

## Слайс E6-S1. Защита границ — 2 единицы

Ценность: типовые атаки на web, файлы и LLM завершаются контролируемым отказом.

Входит:

- CSRF, same-origin, TrustedHost, безопасные headers и CSP;
- пределы body, intent, material, output, time и budget;
- escaping и запрет raw HTML;
- symlink/path traversal проверки;
- секреты только из окружения;
- allowlist handlers и schema validation.

Приёмка: XSS/CSRF/oversize/path traversal/unknown handler имеют отдельные тесты; ошибки не раскрывают внутренние пути, prompt и ключ.

## Слайс E6-S2. Adversarial корпус — 2 единицы

Ценность: известен измеримый остаточный риск prompt injection и подмены режима.

Входит:

- инструкции внутри транскрипции;
- попытка сменить формат, скилл, язык и полномочия;
- fake tool/secret requests;
- delimiter confusion и Markdown/HTML payload;
- regression corpus и отчёт pass/fail.

Приёмка: атака не получает инструменты, не выбирает обработчик после классификации и не обходит parser; остаточные смысловые ошибки документированы.

## Слайс E6-S3. Воспроизводимая поставка — 2 единицы

Ценность: другой разработчик получает одинаковый результат локально и в контейнере.

Входит:

- полный CI gate;
- dependency audit и secret scan;
- non-root multi-stage Docker;
- healthcheck, read-only runtime где возможно;
- pinned lockfile;
- cold-start проверка README.

Приёмка: чистое окружение проходит установку, тесты и запуск только по документации; контейнер не содержит ключ и dev-зависимости.

## Слайс E6-S4. Демонстрация и защита — 2 единицы

Ценность: тестовое задание можно показать за короткий предсказуемый сценарий.

Входит:

- smoke всех пяти режимов;
- protocol → clarification → final → DOCX;
- usage/cost/budget;
- classifier и security eval summary;
- актуальные architecture, threat model, ADR и decision rationale;
- ограничения и roadmap.

As-built: `eval/run_demo.py --offline` повторяет сценарий на FakeLlmGateway
без записи постоянных фикстур. Архитектура §7d/§10 и threat model указывают
`tests/security/`, `eval/`, CI и Docker editable+src. Абсолютная защита от
prompt injection не обещается. Новые пользовательские маршруты не добавляются.

Приёмка: демонстрация повторяется без ручной починки данных; каждый заявленный тезис имеет тест, метрику или документированное ограничение.

## Риски

- Security не начинается в этом эпике: здесь проверяется и усиливается baseline предыдущих эпиков.
- Новые функции ради демонстрации после feature freeze не добавляются.
