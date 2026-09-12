# Выжимка

Роль: qa-tester
Scope: `dd817f1..8a5eabf`
Вердикт: **GATE_OK**

Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Rework закрывает расхождение публичного router и HTTP-каталога: GET API/UI
читают `SkillRegistry.capture()`, внешний `registry.reload()` виден сразу.
Flash хранит только публичную проекцию, живёт 300 с, атомарно погашается и
освобождает ёмкость без вытеснения свежего marker. Reserve+cancel не занимает
слот навсегда. HTML POST при unavailable даёт 303 на страницу каталога, не
сырой JSON 409. Генерация marker ограничена восемью попытками.

Порог branch coverage 85 % соблюдён без добора строк: полный прогон дал
90,39 %. Добавлены только независимые оракулы названных рисков.

# Отчёт

Рецепт: да.

## Договор, предыдущие отчёты и delta

- Прочитан договор E1-S4 в `docs/phases/orchestrator-e1.md` и прошлый
  `docs/handoff/e1-s4-rework-2-qa-tester.md` (`QA-E1S4-R2-001`).
- Сверены rework-2 handoff `security`, `logic-simple`, `perf`, `ux-client`,
  `reviewer` и появившиеся параллельно rework-3 отчёты тех же ролей.
- Проверен диапазон `dd817f1..8a5eabf`; merge-base равен `dd817f1`, HEAD
  равен `8a5eabf`.
- В диапазоне один commit: `8a5eabf fix(web): закрыть замечания третьего
  rework E1-S4`.
- Delta меняет factory, публичный capture реестра, web-каталог/маршруты,
  `app.js`, шаблон и четыре тестовых файла. Зависимости и lock-файл не
  менялись.
- Production, `skills/`, зависимости и lock-файл QA не менял; commit не
  создавался.

## Теория и публичные швы

- Единый владелец каталога: `create_router(registry=...)` без
  `initial_report`. `_CatalogState` больше не копирует поколение. GET
  `/api/skills` и `/skills` без marker вызывают `public_catalog(registry.capture())`.
- Flash — отдельное одноразовое PRG-состояние: reserve до scan, publish
  только `SkillSummary` + человеческий `ReloadSummary`, consume через
  `dict.pop()`, TTL 300 с монотонных часов, ёмкость 8.
- Отказ admission/ёмкости/генерации marker — 303 на фиксированный
  `/skills?notice=reload-unavailable`, затем HTML-каталог.
- Поиск API/UI фильтрует уже построенную публичную проекцию по name/caption
  `casefold` и сохраняет канонический порядок фасада.
- Независимый оракул сравнивает наблюдаемые имена, flash-флаг, число scan,
  status/Location/Content-Type и поля модели; не сравнивает внутренние имена
  `_CatalogState` как единственный критерий и не использует `sleep` как
  оракул.

## Матрица приёмки и рисков

| Риск | Наблюдаемый исход | Статус |
|---|---|---|
| Успех | Пять режимов, API/UI parity, канонический порядок `alpha, zeta` при несовпадении каталога и `name` | пройдено |
| Отказ | Busy/полная ёмкость: 303 → HTML-каталог с alert, текущие карточки, не JSON 409 | пройдено |
| Неверный ввод | Query 128/129, неизвестный/malformed marker, Unicode CSRF — предсказуемый отказ без scan | пройдено |
| Правило домена | GET читает текущий snapshot того же `SkillRegistry`; внешний `reload()` виден API и UI; файлы не исполняются | пройдено |
| Повтор | Marker потребляется первым GET; повтор — current без flash; идемпотентный reload не дублирует карточки | пройдено |
| Атака | CSRF/Origin до admission; только публичные поля; machine `reason` и `body` не в flash; CSP/Host/CORS off | пройдено |

Закрытие целевых пунктов дельты:

- Публичный router после `registry.reload()`: report, `registry.search("")`,
  `GET /api/skills` и `GET /skills` дают одно поколение; пустая сборка затем
  прямой reload тоже виден HTTP — пройдено.
- Flash TTL/очистка: на `t+299.999` девятый POST недоступен; на `t+300`
  ёмкость возвращается; истёкший Location показывает current без flash;
  свежий recovered Location сохраняет свой flash — пройдено.
- Только публичная проекция: flash — `SkillSummary`, нет `report`, нет
  `body`, `model_dump` issue = `{message, action}`, нет
  `missing_description` — пройдено.
- Reserve+cancel не вечный слот: unit `reserve → block → discard → reserve`;
  HTTP cancel после scan допускает следующие 8 POST — пройдено.
- HTML POST unavailable → страница каталога, не сырой JSON: 303,
  `text/html`, alert, пять/текущие карточки, нет `{"error"` и машинного
  `reload_unavailable` в теле — пройдено.
- Marker generation bounded: 8 столкновений до scan, gate свободен, после
  consume следующий POST проходит — пройдено.

Остальной контракт E1-S4 сохранён существующим набором: 5/0, 5/1, XSS,
strict Host/Origin, PRG, локальная статика, доступная семантика.

## Determinism, mutant и weak-oracle audit

- Конкурентные burst/cancel/consume и новые unit-оракулы TTL/reserve прошли
  20/20 повторов без `sleep`.
- In-memory mutants не меняли production на диске:
  - `discard_reservation` → no-op — убит unit reserve/discard;
  - `_purge_expired` → no-op — убит unit TTL/ёмкость;
  - `expires_at = now` — убит consume свежего marker;
  - flash issue = machine `reason` — убит public-projection oracle;
  - бесконечный `_new_marker` — не проявляется на unit без коллизий; убит
    HTTP `test_marker_collisions_stop_before_scan_and_release_gate`
    (потолок 8 вызовов и отказ до scan);
  - отдельное web-поколение — убит
    `test_public_router_registry_cannot_diverge_from_http_catalog`;
  - JSON 409 вместо 303 — убит unavailable-redirect/HTML oracle.
- Итог: 6/6 целевых mutants убиты.
- Прежний HTTP TTL-тест не потреблял recovered Location и принял бы
  немедленное истечение свежего flash: добавлен GET recovered marker и
  отдельный unit на 8 истёкших + 1 свежий.
- HTML разбирается через `HTMLParser`; full-page golden нет.
- 303/HTML проверяются по status, Location, `Content-Type` и отсутствию
  JSON-envelope, а не по приватному lock.

## Изменения QA

- `tests/test_e1_s4_rework_3.py`: unit reserve+discard, unit TTL без потери
  свежего marker, усиленная публичная проекция, consume recovered flash в
  HTTP TTL, `text/html` вместо JSON на unavailable.
- Добавлен этот handoff.
- Production, существующие тесты кроме указанного файла, `skills/`,
  зависимости и lock-файл QA не менял; commit не создавался.

## Команды и результаты

- Focus:
  `uv run --no-sync pytest -q tests/test_e1_s4_rework_3.py --no-cov` →
  `7 passed`.
- Полный финальный:
  `uv run --no-sync pytest -q -rs` →
  `319 passed, 4 skipped`, branch coverage `90,39 %`; порог 85 % достигнут.
- Конкурентный и unit repeat-run → `20/20 deterministic runs passed`.
- In-memory mutation audit → `6/6` целевых mutants убиты.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно, 157 файлов.
- `uv run --no-sync mypy src tests` → успешно, 49 source files.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- `git diff --check dd817f1..8a5eabf` → успешно.

## Skips

- 3 Windows symlink cases пропущены из-за отсутствующей привилегии создания
  ссылок (`WinError 1314`).
- 1 FIFO case пропущен как POSIX-only.
- Эти skips относятся к принятой файловой границе S2 и не скрывают S4-риски
  дельты.

## Итог по severity

- P0: 0.
- P1: 0.
- P2: 0.
- P3: 0.

**GATE_OK.**
