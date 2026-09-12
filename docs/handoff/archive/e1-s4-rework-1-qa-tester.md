# Выжимка

Роль: qa-tester
Scope: `11393ba..7fe15c7`
Вердикт: **BLOCKED**

Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Rework закрывает строгий Host, Unicode CSRF, согласованность одного поколения,
starvation общего worker pool, очистку отменённой очереди, человеческие
сообщения и основную схему PRG. Gate блокирует один дефект: reload-marker
остаётся повторно используемым и снова показывает старый flash-результат при
повторном GET того же URL.

Порог branch coverage 85 % соблюдён без добора строк: полный прогон показал
89,95 %. Добавлены только риск-ориентированные проверки.

# Отчёт

Рецепт: да.

## Scope, предыдущие handoff и delta

- Прочитаны все шесть предыдущих S4 handoff: `qa-tester`, `security`,
  `reviewer`, `logic-simple`, `perf`, `ux-client`.
- Проверен диапазон `11393ba..7fe15c7`; merge-base равен `11393ba`, HEAD
  worktree равен `7fe15c7`.
- В диапазоне один коммит: `7fe15c7 fix(web): устранить замечания E1-S4`.
- Delta разделяет web-код на `_catalog`, `_host_guard`, `_issue_messages`,
  `_reload_guard`, добавляет согласованный `LoadReport.search()`, асинхронный
  admission gate, PRG и UI-фокус результата.
- До QA worktree был чист. Production, скиллы, зависимости и lock-файл QA не
  менял.

## Матрица приёмки и рисков

- Strict Host: обычный чужой host, trusted-prefix suffix, userinfo,
  path/query/fragment, пустой/нечисловой/нулевой/слишком большой порт,
  лишний двоеточие, сверхдлинный порт, duplicate Host и missing Host получают
  безопасный 400. `[::1]`, `[::1]:8000` и `::1` также закрыты, поскольку
  поддерживаемый bind и allowlist остаются IPv4/localhost-only — пройдено.
- Разрешённые authority: `localhost`, `127.0.0.1`, `testserver`, порты
  `1..65535` — пройдено.
- Origin/Host: проверены http/https, неявные 80/443, совпадающие явные порты,
  mismatch порта и mismatch scheme. Допустимые пары дают 303, отклонённые —
  403 до loader call — пройдено.
- Unicode CSRF: `csrf_token=%D1%8F` даёт точный 403
  `reload_forbidden`, loader не вызывается, ссылка snapshot не меняется —
  пройдено.
- Generation consistency: два управляемых через `threading.Event` POST и
  промежуточные GET `/skills` + `/api/skills` возвращают карточки, issue и
  отчёт только одного поколения. Первый marker после публикации второго
  поколения всё ещё открывает именно первый результат — пройдено.
- Worker tokens: первый loader удерживается событием, второй POST ждёт
  асинхронный gate до `run_in_threadpool`; синхронный probe получает общий
  worker token до release первого loader — пройдено.
- Cancellation cleanup: второй POST детерминированно сигнализирует вход в
  занятый async gate, отменяется и полностью завершается до release первого.
  Третий POST вызывает loader как второй, а не третий вызов. `sleep` из этого
  oracle удалены — пройдено.
- PRG: POST отвечает 303 на `/skills?reload=<marker>`, CSRF-токен в Location
  отсутствует, обычный canonical refresh не повторяет POST, результат
  фокусируется, JavaScript удаляет query через `history.replaceState` —
  пройдено.
- Одноразовость marker: первый GET показывает flash, но второй GET того же
  Location повторяет старый flash — **не пройдено**, P2.
- Human issues: partial `missing_description`, missing root, неизвестная
  skill-причина и неизвестная root-причина показывают русское объяснение и
  действие. Машинные reason, абсолютный путь, тело и root-идентификатор `.`
  не отображаются — пройдено.
- Внешняя поверхность не изменилась: ровно пять прикладных маршрутов
  (`GET /health`, `GET /`, `GET /api/skills`, `GET /skills`,
  `POST /skills/reload`) — пройдено.
- API/UI parity, стабильный порядок, четыре публичных поля, поиск, 128/129,
  пустые состояния, 5/0, 5/1, idempotent reload, локальная статика и
  accessibility semantics — пройдено.
- XSS: hostile query и metadata остаются текстом; body не публикуется,
  dangerous nodes и inline script не возникают, JavaScript использует
  `textContent` и не содержит известных HTML execution sinks — пройдено.
- CSP, nosniff, XFO, referrer policy, request ID и отсутствие CORS сохранены
  на success и проверенных отказах — пройдено.

## Finding

### QA-E1S4-R1-001 — P2 — reload-marker не потребляется после первого GET

Новый regression test
`tests/test_e1_s4_qa.py::test_reload_marker_is_consumed_after_first_get`
выполняет один валидный POST, получает 303 Location, затем дважды делает GET
этого же URL.

Наблюдаемый результат:

- первый GET показывает «Каталог перезагружен»;
- второй GET того же `/skills?reload=1` снова показывает тот же результат;
- loader вызван ровно один раз, то есть повторяется не мутация, а устаревшее
  подтверждение.

Причина находится в `src/skillhub/web/_catalog.py:109-118`: `for_marker()`
ищет поколение в `_flashes`, но не удаляет найденный элемент. Клиентский
`history.replaceState` в `src/skillhub/web/static/app.js:4-12` очищает URL
только после выполнения JavaScript и не делает marker одноразовым на сервере.
Сохранённый Location, дубликат вкладки, повторный прямой GET или работа без
JavaScript воспроизводят старый success/partial результат.

Это P2: нарушен явно проверяемый контракт одноразового результата PRG и
пользователь может принять устаревший отчёт за новый. Повторного POST, утечки
секрета и повреждения snapshot нет, поэтому это не P0/P1.

Критерий закрытия: первый GET известного marker возвращает связанное поколение
с `reload_performed=true` и атомарно потребляет marker; повторный/неизвестный
marker возвращает текущий каталог без flash. При конкурентных GET только один
запрос должен получить одноразовый результат.

## Mutant audit

Production на диске не мутировался.

- In-memory `_valid_decimal_port()` → всегда `True` — убит strict Host
  oracle: malformed numeric ports начинают отвечать 200 вместо 400.
- In-memory Unicode-aware `_tokens_match()` → старый строковый
  `secrets.compare_digest()` — убит Unicode CSRF oracle: ответ становится 500
  вместо 403.
- Разделение `report/skills` между поколениями убивается
  `test_two_reloads_and_get_keep_each_generation_exact`.
- Перенос ожидания reload-lock внутрь общего worker pool убивается
  `test_waiting_reload_does_not_exhaust_general_worker_pool`.
- Сохранение отменённого waiter убивается детерминированным
  `test_cancelled_waiting_reload_does_not_run_later` по числу loader calls.
- Непотребляемый marker — живое поведение target, пойманное новым failing
  regression test и оформленное как `QA-E1S4-R1-001`.

Итог выполненных in-memory проб: 2/2 mutants убиты. Структурный audit
подтвердил ещё три сильных oracle и один реальный surviving defect.

## Weak / brittle audit

- Предыдущий PRG-тест проверял новый canonical `/skills`, но не повторял
  исходный Location; поэтому название «once» было сильнее фактического
  oracle. Добавлен повтор того же marker URL.
- В Host matrix не было IPv6 authority и совместной проверки
  scheme/host/default-port на POST. Пробел закрыт.
- Unicode CSRF раньше доказывал неизменность snapshot косвенно. Новый oracle
  дополнительно считает loader calls и требует ноль.
- Cancellation-тест полагался на `asyncio.sleep(0)`. Он заменён наблюдаемым
  async lock и событиями: тест знает, что второй запрос действительно вошёл в
  очередь, прежде чем отменять его.
- Добавлен точный route/method oracle после внутреннего рефакторинга.
- Golden HTML, wall-clock ожидания и новые проверки внутренних пробелов
  шаблона не добавлялись.

## Изменения QA

- `tests/test_e1_s4_qa.py`: IPv6 и Origin/Host/port matrix, прямой Unicode
  no-reload oracle, одноразовый marker regression, root-message fallback и
  точная поверхность маршрутов.
- `tests/test_skills_catalog.py`: cancellation cleanup переведён с
  scheduler-yield/sleep на детерминированный наблюдаемый async gate.
- `docs/handoff/e1-s4-rework-1-qa-tester.md`: этот отчёт.

## Команды и результаты

- Baseline:
  `uv run --no-sync pytest -q -rs` →
  `270 passed, 4 skipped`, branch coverage `89.95%`.
- Focus:
  `uv run --no-sync pytest -q tests/test_e1_s4_qa.py
  tests/test_skills_catalog.py --no-cov` →
  `76 passed, 1 failed`; единственный отказ —
  `test_reload_marker_is_consumed_after_first_get`.
- Полный финальный:
  `uv run --no-sync pytest -q -rs` →
  `281 passed, 1 failed, 4 skipped`, branch coverage `89.95%`;
  порог 85 % достигнут.
- Контроль без зафиксированного дефекта:
  `uv run --no-sync pytest -q -rs -k
  "not reload_marker_is_consumed_after_first_get"` →
  `281 passed, 4 skipped, 1 deselected`, branch coverage `89.95%`.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно, 142 файла.
- `uv run --no-sync mypy src tests` → успешно, 47 source files.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- McCabe ≤ 3 для изменённых production Python-файлов → успешно.
- `git diff --check 11393ba..7fe15c7` и `git diff --check` → успешно.
- In-memory security mutants → 2/2 убиты.

## Skips и ограничения

- 3 Windows symlink cases пропущены из-за отсутствующей привилегии создания
  ссылок (`WinError 1314`).
- 1 FIFO case пропущен как POSIX-only.
- Эти skips относятся к ранее принятой файловой границе и не скрывают
  найденный PRG defect.

## Итог по severity

- P0: 0.
- P1: 0.
- P2: 1 OPEN (`QA-E1S4-R1-001`).
- P3: 0.

**GATE_OK не выдан.**
