# Выжимка

Роль: qa-tester
Scope: `6da2a10..0da3ae5`
Вердикт: **BLOCKED**

Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Rework закрывает канонический порядок при несовпадающих именах каталогов,
небезопасный публичный handle в `app.state`, очередь reload, повторный scan,
replay flash-marker, вытеснение старого результата, неявную конфигурацию CSRF
и приватный импорт web из composition root.

Gate блокирует один подтверждённый дефект: публичный `create_router()` сохраняет
переданный `SkillRegistry`, но HTTP читает отдельное поколение. Штатный
`registry.reload()` после сборки меняет публичный snapshot реестра, а API и UI
бессрочно остаются на старом поколении.

Порог branch coverage 85 % соблюдён без добора строк: полный прогон дал
90,21 %. Добавлены только тесты наблюдаемых рисков.

# Отчёт

## Scope, предыдущие отчёты и delta

- Прочитаны исходные и rework-1 handoff всех шести S4-ролей: `qa-tester`,
  `security`, `logic-simple`, `perf`, `ux-client`, `reviewer`.
- Также сверены появившиеся параллельно rework-2 handoff `security`,
  `logic-simple`, `perf`, `ux-client`, `reviewer`.
- Проверены договор E1-S4 в `docs/phases/orchestrator-e1.md`, текущий
  production-код и полный diff `6da2a10..0da3ae5`.
- В диапазоне один commit:
  `0da3ae5 fix(web): close E1-S4 rework findings`; merge-base равен
  `6da2a10`.
- Delta меняет README, семь production-файлов и три существующих test-файла.
  Зависимости и lock-файл не менялись.
- Baseline до QA был чист относительно target и дал
  `294 passed, 4 skipped`, branch coverage `90,11 %`.

## Матрица приёмки и рисков

- Канонический порядок: каталоги `alpha-directory/name=zeta` и
  `zeta-directory/name=alpha` дают один порядок `alpha, zeta` в
  `LoadReport.search()`, `SkillRegistry.search()`, JSON и HTML — пройдено.
- Standard composition: `create_app()` больше не сохраняет
  `SkillRegistry` ни в `app.state.skill_registry`, ни в значениях
  `app.state` — пройдено.
- Public router authority: после поддерживаемого `registry.reload()` реестр
  видит новый `bravo`, а API/UI остаются на старом `alpha` — **не пройдено,
  P2**.
- Неблокирующий single-flight: при остановленном первом scan burst из
  12 валидных POST полностью завершается с безопасным 409 до освобождения
  loader; число вызовов loader остаётся 1 — пройдено.
- Worker availability: при ёмкости общего AnyIO pool 2 один worker удерживается
  scan, а синхронный GET probe завершается вторым worker до release loader —
  пройдено.
- Последующий admission: после завершения активного reload следующий POST
  принимается и выполняет ровно следующий scan — пройдено существующим и
  усиленным oracle.
- Cancellation: задача детерминированно отменяется после завершения scan, но
  до `remember_reload()`. Gate освобождается, отменённый запрос не расходует
  flash-slot: следующие восемь POST получают 303, девятый — 409; всего
  выполнено ровно 9 scan — пройдено.
- Random marker: проверены вызов генератора с 32 байтами, collision retry,
  два разных 43-символьных marker и отсутствие последовательного значения —
  пройдено.
- Pop / unknown / replay: неизвестный marker не отражается и показывает
  current без flash; первый точный GET атомарно получает старое поколение,
  повтор того же URL показывает current без flash — пройдено.
- Exact generation: старый marker после более новой публикации сохраняет
  только свои cards/issues; данные поколений не смешиваются — пройдено.
- Concurrent consume: два одновременных GET одного старого marker делятся
  точно на один `alpha + flash` и один `bravo current + no flash` — пройдено.
- Capacity: восемь outstanding redirect сохраняются, девятый POST получает
  409 до scan; потребление одного marker освобождает слот и следующий POST
  выполняет девятый scan — пройдено.
- Ответ 409 использует точный bounded envelope, CSP, `nosniff`, XFO,
  referrer policy, request ID и не добавляет CORS — пройдено.
- Public `create_router()`: URL-safe ASCII secrets точной длины 1 и 256
  принимаются; пустая строка, Unicode, whitespace и 257 символов fail-fast
  дают `ValueError` — пройдено.
- Unicode request token `%D1%8F` даёт 403 `reload_forbidden` и не вызывает
  второй scan — пройдено.
- Public web facade: `app_factory.py` импортирует только `skillhub.web`, а
  `__all__` содержит router, error handlers и все три middleware —
  пройдено.
- Остальной E1-S4 контракт сохранён существующим полным набором: пять
  режимов, API/UI parity, query 128/129, partial/missing root, XSS, strict
  Host/Origin, CORS off, PRG, локальная статика и доступная семантика.

## Finding

### QA-E1S4-R2-001 — P2 — публичный registry расходится с HTTP-каталогом

Новый regression test
`tests/test_e1_s4_rework_2_qa.py::test_public_router_registry_cannot_diverge_from_http_catalog`
использует только публичные `SkillRegistry` и `skillhub.web.create_router()`:

1. Загружает `alpha` и передаёт тот же registry с его report в router.
2. Добавляет валидный `bravo`.
3. Вызывает публичный `registry.reload()`.
4. Сверяет `registry.search("")`, `GET /api/skills` и `GET /skills`.

Наблюдаемый результат:

- report и `registry.search("")`: `["alpha", "bravo"]`;
- API и HTML: `["alpha"]`.

Причина: `_CatalogState.__init__()` копирует `initial_report` в отдельный
`_current`, а обновляет его только приватный путь `_CatalogState.reload()`.
Переданный registry остаётся у caller и его публичный `reload()` меняет только
registry snapshot. Удаление `app.state.skill_registry` закрывает стандартный
factory, но не публично экспортированный router seam.

Это P2: поддерживаемый публичный API допускает постоянный stale HTTP-каталог и
нарушает единый фасад/поколение. Данные не повреждаются и security boundary не
обходится, поэтому severity не P0/P1.

Критерий закрытия: у router должен быть один неразделимый владелец публикации.
После любой поддерживаемой публичной перезагрузки registry, API и HTML обязаны
захватывать одно поколение; альтернативно seam с независимо изменяемой парой
`registry + initial_report` не должен оставаться публичным контрактом.

## Determinism, mutant и weak-oracle audit

- Три конкурентных сценария — burst admission, cancellation after scan и
  concurrent marker consume — прошли 20/20 повторов без `sleep`.
- Синхронизация использует события, cancellation checkpoint и фактическое
  завершение HTTP-ответов; wall-clock timeout служит только аварийной границей.
- In-memory mutants не меняли production на диске:
  - удаление canonical sort — убито mismatched directory/frontmatter oracle;
  - удаление nonblocking admission — убито burst/no-second-scan/worker oracle;
  - возврат replayable marker — убит pop/replay oracle;
  - удаление capacity check — убито reject-before-scan oracle;
  - возврат configured-token к фиксированной длине 43 — убит boundary 1 oracle;
  - удаление fail-fast config validation — убито invalid-config oracle.
- Итог: 6/6 целевых mutants убиты.
- Marker oracle проверяет collision retry, а не статистическую «случайность».
- HTML разбирается через `HTMLParser`; full-page golden, зависимость от
  whitespace и сравнение случайных целых страниц не добавлены.
- 409 проверяется по наблюдаемому status/envelope/headers и числу loader calls,
  а не по приватному состоянию lock.
- Capacity oracle дополнительно доказывает восстановление после consume, чтобы
  не принять только отказ без освобождения слота.
- Единственный failing oracle сравнивает три публичных read-пути и не зависит
  от внутренних имён `_CatalogState`.

## Изменения QA

- Добавлен `tests/test_e1_s4_rework_2_qa.py`: 15 test cases, из них один
  фиксирует подтверждённый дефект.
- Добавлен этот handoff.
- Production, существующие тесты, `skills/`, зависимости и lock-файл QA не
  менял; commit не создавался.

## Команды и результаты

- Baseline:
  `uv run --no-sync pytest -q -rs` →
  `294 passed, 4 skipped`, branch coverage `90,11 %`.
- Focus:
  `uv run --no-sync pytest -q tests/test_e1_s4_rework_2_qa.py --no-cov` →
  `14 passed, 1 failed`; единственный отказ —
  `test_public_router_registry_cannot_diverge_from_http_catalog`.
- Полный финальный:
  `uv run --no-sync pytest -q -rs` →
  `308 passed, 1 failed, 4 skipped`, branch coverage `90,21 %`;
  threshold 85 % достигнут, gate красный из-за finding.
- Контроль без зафиксированного дефекта:
  `uv run --no-sync pytest -q -rs -k
  "not public_router_registry_cannot_diverge_from_http_catalog"` →
  `308 passed, 4 skipped, 1 deselected`, branch coverage `90,21 %`.
- Конкурентный repeat-run → `20/20 deterministic concurrency runs passed`.
- In-memory mutation audit → `6/6 in-memory mutants killed`.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно; новый test-файл
  дополнительно перепроверен после записи finding.
- `uv run --no-sync mypy src tests` → успешно; новый test-файл дополнительно
  перепроверен после записи finding.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- McCabe ≤ 3 для изменённых production Python-файлов → успешно.
- `git diff --check 6da2a10..0da3ae5` → успешно.

## Skips

- 3 Windows symlink cases пропущены из-за отсутствующей привилегии создания
  ссылок (`WinError 1314`).
- 1 FIFO case пропущен как POSIX-only.
- Эти skips относятся к принятой файловой границе S2 и не скрывают finding
  публичного router.

## Итог по severity

- P0: 0.
- P1: 0.
- P2: 1 OPEN (`QA-E1S4-R2-001`).
- P3: 0.

**GATE_OK не выдан.**
