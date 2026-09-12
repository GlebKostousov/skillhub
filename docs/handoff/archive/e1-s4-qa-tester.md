# Выжимка

Роль: qa-tester
Scope: `e4feefa..f2c6b9f`
Вердикт: **BLOCKED**

Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Каталог, стартовая загрузка, API/UI parity, поиск, reload, безопасные ошибки,
XSS/CSRF/CORS, request ID, семантика страницы и локальная статика подтверждены.
Открыт один дефект Host boundary: malformed authority с доверенным именем до
первого двоеточия проходит allowlist и получает `200`.

Порог branch coverage 85 % соблюдён: итоговый прогон дал 89,74 %. Процент не
использовался как цель; добавлены только тесты наблюдаемых рисков.

# Отчёт

Рецепт: да.

## Договор, канон и delta

Проверены:

- договор E1-S4 в `docs/phases/orchestrator-e1.md`;
- `docs/phases/epic-e1-foundation-registry.md`, `CONTEXT.md`;
- `docs/architecture/skillhub.md`,
  `docs/architecture/decision-rationale.md`;
- `docs/security/threat-model.md`;
- production-код, пять артефактов `skills/`, шаблоны, JavaScript, CSS и весь
  существующий набор тестов.

Диапазон содержит один коммит
`f2c6b9f feat: добавить каталог пяти скиллов`; merge-base равен `e4feefa`.
В delta добавлены ровно пять каталогов скиллов, startup reload, JSON и HTML
каталог, поиск, защищённая HTML-форма reload и локальные UI-ресурсы.

## Матрица приёмки и рисков

- Ровно пять артефактов:
  `business-message`, `meeting-action-items`, `meeting-protocol`,
  `text-summary`, `text-translation`; посторонних каталогов нет. У
  `meeting-protocol` ровно два ожидаемых reference-файла — пройдено.
- Startup reload: фабрика вызывает `reload()` ровно один раз до первого
  запроса и публикует пять элементов — пройдено.
- `GET /api/skills?q=`: стабильный порядок по `name`, только поля `name`,
  `caption`, `description`, `has_files`, без `body` и абсолютного пути —
  пройдено.
- `GET /skills?q=`: те же элементы, порядок и значения всех четырёх полей;
  `has_files` отображается правильным текстом, дубликатов нет — пройдено.
- Поиск: name и caption проверены отдельно, включая Unicode
  `Straße`/`STRASSE`; пустой запрос возвращает всё, no-match возвращает
  пустые API/UI состояния — пройдено.
- Длина query: 128 символов принимаются обоими представлениями, 129 дают
  согласованный безопасный 422; snapshot не меняется — пройдено.
- Штатный reload: `5/0`, повторный POST также `5/0`, карточки не
  дублируются — пройдено.
- Битый шестой: `5/1`, пять валидных остаются, показываются только безопасные
  `path/reason`, тело и абсолютный путь отсутствуют — пройдено существующим
  тестом.
- Existing empty root: startup пуст, reload `0/0`; missing root: пустой
  API/UI и безопасный `root_missing`, reload `0/1`, без 500 — пройдено.
- Идемпотентность: повторный reload сохраняет значения и порядок без
  дублей — пройдено.
- XSS: hostile caption/description и отражённый `q` остаются текстом;
  inline script/event handler/iframe/object не появляются — пройдено.
- Unsafe sinks: единственный локальный `app.js` использует `textContent`;
  `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`,
  `eval` и `new Function` отсутствуют — пройдено.
- CSRF + Origin matrix: missing/null/foreign/duplicate Origin,
  missing/wrong/duplicate token, лишнее поле, invalid UTF-8,
  unsupported media type и oversized body отклоняются точными
  403/415/413 — пройдено.
- Snapshot on reject: во всей матрице сохраняется та же ссылка snapshot;
  добавленный на диск `bravo` не публикуется — пройдено.
- CORS: preflight для API, HTML и reload получает безопасный 405 без любого
  `Access-Control-*` — пройдено.
- Security headers/request ID: CSP, nosniff, same-origin referrer,
  `X-Frame-Options: DENY` и уникальный 32-hex request ID есть на success,
  framework и typed-error ответах — пройдено.
- Host boundary: обычный чужой Host получает безопасный JSON 400, но
  malformed trusted-prefix Host проходит — **не пройдено**, P2.
- API errors: overlong query, reload rejection, unknown path и unsupported
  method используют ограниченную оболочку; query/path/body/token не
  отражаются в ответе или журнале — пройдено.
- UI semantics: русский `lang`, один H1, навигация, skip target, label,
  GET search, POST reload, live status и понятные empty/partial/success
  состояния — пройдено.
- Статика: favicon, pinned Bootstrap, CSS и JS загружаются только с
  same-origin `/static/`; все четыре ресурса доступны — пройдено.

## Findings

### QA-E1S4-001 — P2 — TrustedHost принимает malformed Host с доверенным префиксом

Новый regression test
`tests/test_e1_s4_qa.py::test_malformed_and_trusted_prefix_hosts_are_rejected`
ожидает безопасный 400 для:

- `localhost:443@attacker.example`;
- `localhost:80.attacker.example`;
- `testserver:evil`;
- `testserver:999999`.

Фактически все четыре запроса к `/skills` получают `200`. Текущая обёртка
`TrustedHostEnvelopeMiddleware` делегирует проверку реализации Starlette,
которая выделяет hostname через `Host.split(":")[0]`. Поэтому malformed
authority после доверенного префикса не проверяется. Для числового
`testserver:999999` значение также попадает в абсолютные URL локальных
ресурсов, ломая целостность выдаваемой страницы.

Риск ограничен локальным HTTP-контуром и malformed прямым запросом, поэтому
severity P2, а не P0/P1. Но наблюдаемый договор строгого trusted Host и
явно запрошенный hostile-Host gate нарушены.

Рецепт исправления: до делегирования TrustedHost строго разобрать единственный
Host как разрешённое локальное имя плюс необязательный десятичный порт
`1..65535`; отклонять userinfo, path/query/fragment, лишние двоеточия,
нечисловой/вне диапазона порт и повторный Host. Валидные
`localhost`, `127.0.0.1`, `testserver` с допустимым портом должны сохраниться.

## Mutant audit

In-memory mutants не меняли production на диске.

- `casefold()` → `lower()` в поиске — убит Unicode caption oracle.
- Граница query `> 128` → `>= 128` — убита точной проверкой 128/129 и
  согласованности API/UI.
- Удаление обязательного Origin — убито `missing-origin` и проверкой
  непубликации snapshot.
- Полный обход `validate_reload_request()` — убит wrong-token сценарием и
  проверкой статуса/ссылки snapshot.

Итог: 4/4 целевых mutants убиты. Во время аудита первая версия нового
casefold-теста оказалась слабой: имя `strasse-mode` само совпадало с query и
маскировало замену `casefold()` на `lower()`. Фикстура исправлена на независимое
имя `unicode-caption`, после чего mutant падает на caption-сценарии.

Live Host-дефект эквивалентен выжившему security mutant: проверка только
подстроки до первого `:`. Он зафиксирован падающим regression test.

## Weak/brittle audit

- Existing parity test сравнивал только имена карточек. Новый oracle сверяет
  порядок и все публичные поля каждой карточки.
- Existing default API test искал строку `Безопасные инструкции`, которой нет
  в реальных пяти body; такой oracle частично vacuous. Новый тест берёт
  фактические body из опубликованного snapshot и проверяет их отсутствие в
  обоих HTTP-представлениях вместе с точным набором JSON-полей.
- Existing HTTP search cases не отличали `lower()` от `casefold()`. Добавлен
  независимый Unicode caption case.
- Existing hostile Host test покрывал только обычный чужой hostname и не
  проверял malformed trusted prefix. Новый тест обнаружил finding.
- Existing CSRF matrix проверяла значения snapshot, но не его ссылочную
  неизменность и не все body/content-type/duplicate-header ветви. Новый
  matrix проверяет оба свойства.
- Новые структурные проверки используют `HTMLParser`; golden HTML, снимки
  целых страниц, sleep и привязка к пробельному форматированию не добавлены.
- Тексты success/error и названия полей проверяются только там, где они
  являются публичным договором.

## Изменения QA

- Добавлен `tests/test_e1_s4_qa.py`: 24 риск-ориентированных test cases.
- Добавлен этот handoff.
- Production, `skills/`, зависимости, lock-файл и commit не менялись.

## Команды и результаты

- Baseline до QA:
  `uv run --no-sync pytest -q -rs` →
  231 passed, 4 skipped, coverage 89,52 %.
- Focus:
  `uv run --no-sync pytest -q tests/test_e1_s4_qa.py --no-cov` →
  23 passed, 1 failed; единственный отказ —
  `test_malformed_and_trusted_prefix_hosts_are_rejected`.
- Полный финальный:
  `uv run --no-sync pytest -q -rs` →
  254 passed, 1 failed, 4 skipped, coverage 89,74 %; threshold 85 % достигнут,
  gate красный из-за finding.
- Контроль без зафиксированного дефекта:
  `uv run --no-sync pytest -q -rs -k
  "not malformed_and_trusted_prefix_hosts_are_rejected"` →
  254 passed, 4 skipped, 1 deselected, coverage 89,74 %.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно, 135 файлов.
- `uv run --no-sync mypy src tests` → успешно, 43 source files.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- McCabe ≤ 3 по изменённым production-файлам E1-S4 → успешно.
- `git diff --check e4feefa..f2c6b9f` и `git diff --check` → успешно.
- In-memory mutation runs → 4/4 mutants убиты после усиления casefold oracle.

## Skips и ограничения

- 3 Windows symlink cases пропущены из-за отсутствующей привилегии создания
  ссылок (`WinError 1314`).
- 1 FIFO case пропущен как POSIX-only.
- Эти skips относятся к ранее принятой файловой границе S2 и не скрывают
  E1-S4 HTTP/UI/Host finding.

## Итог по severity

P0: 0.
P1: 0.
P2: 1 OPEN (`QA-E1S4-001`).
P3: 0.
