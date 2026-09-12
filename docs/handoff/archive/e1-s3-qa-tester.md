# Выжимка

Роль: qa-tester
Scope: `df21067..58e2750`
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Договор E1-S3 подтверждён через публичные `snapshot`, `load()`, `reload()` и
`search()`. Снимок и `Skill` неизменяемы, старые ссылки сохраняют прежние
значения, `load()` и поиск не публикуют состояние, а один `reload()` заменяет
ссылку ровно один раз. Конкурирующие reload сериализуются с
`max_active == 1`; четыре читателя без блокировки видят только полные старый
или новый снимки.

Поиск использует полноценный Unicode casefold для name/caption, а несколько
совпадений, пустой запрос и отсутствие совпадений сохраняют канонический
порядок и форму результата. Битый шестой каталог, missing/empty root и
идемпотентный reload возвращают точные loaded/skipped. Финальный результат:
200 passed, 4 ожидаемых platform skip, branch coverage 89,21 % при пороге
85 %. Покрытие использовалось как gate, а не как цель выбора тестов.

# Отчёт

Рецепт: да.

## Scope и источники истины

Проверен committed diff `df21067..58e2750`, один commit
`58e2750 feat(registry): добавить атомарный снимок`.

Основной договор: `docs/phases/orchestrator-e1.md`, раздел «Слайс E1-S3 —
Атомарный снимок, reload и поиск». Термины и ожидаемое поведение сверены с
`CONTEXT.md`, `docs/phases/epic-e1-foundation-registry.md`,
`docs/architecture/skillhub.md` и `docs/security/threat-model.md`.
Проверены production-код `_models.py`/`_registry.py`, прежние registry-тесты и
новый `tests/test_registry_snapshot.py`.

QA изменил только `tests/test_registry_snapshot.py` и этот handoff.
Production-файлы, зависимости, lock-файл, skills и commit не менялись.

## Матрица рисков

- P0, внешняя мутация опубликованного состояния: mapping отвергает запись,
  `Skill` отвергает изменение поля, старая mapping/Skill-ссылка после reload
  содержит только прежние значения — пройдено.
- P1, частичная публикация: readers получают только полные
  `("alpha", "bravo")` или `("charlie", "delta")`; пустого и смешанного
  состояния нет — пройдено.
- P1, параллельные reload: второй loader не входит до выхода первого,
  `max_active == 1` — пройдено.
- P1, блокировка читателей: четыре reader-задачи читают `snapshot` и
  `search("")`, пока reload удерживает сериализующий lock; дедлока и
  исключений нет — пройдено.
- P2, лишняя публикация: изменённое дерево через `load()` возвращает новый
  отчёт, но не меняет заполненный снимок; `search()` также не меняет ссылку;
  каждый `reload()` публикует ровно один раз — пройдено.
- P2, поиск: uppercase name-query, Unicode `Straße`/`STRASSE`, несколько
  caption-совпадений с порядком caption и каталогов, отличным от порядка
  name, пустой query и no-match дают ожидаемый tuple — пройдено.
- P2, частичный файловый отказ: пять валидных каталогов публикуются, битой
  шестой даёт `missing_description`, loaded 5, skipped 1 — пройдено.
- P2, missing/empty root: оба публикуют предсказуемый пустой снимок; missing
  даёт loaded 0/skipped 1, empty — 0/0 — пройдено.
- P3, replay: два reload неизменённого дерева возвращают равные отчёты,
  loaded 2/skipped 0 и одинаковый канонический порядок — пройдено.
- Регрессия E1-S1/E1-S2 и coverage gate 85 % — пройдено.

## Mutant audit

Mutants применялись только в памяти отдельных Python-процессов; production
на диске не изменялся.

- Mutant, публикующий из `load()`, убит двумя QA-оракулами: заполненный
  snapshot сменился до reload, а счётчик публикаций изменился на reader-вызове.
- Mutant с промежуточной пустой публикацией внутри `reload()` убит точным
  счётчиком: две замены вместо одной.
- Mutant `lower()` вместо `casefold()` убит `Straße`/`STRASSE`.
- Mutant, сортирующий результаты поиска по caption, убит несовпадающим
  порядком caption/name.
- Mutant без reload-lock убит сериализационным оракулом: второй loader вошёл
  до освобождения первого, а ожидаемая вторая попытка lock не состоялась.
- Mutants с обычным изменяемым dict или in-place обновлением снимка убиваются
  запретом записи и сохранением старой ссылки.

Живых mutants в заявленных рисках не обнаружено.

## Аудит тестов

- Исходный suite закрывал happy path, immutable mapping/Skill, старую ссылку,
  casefold одного результата, empty/no-match, broken sixth, missing/empty,
  два reload и четыре reader.
- Weak gap: непубликующий `load()` проверялся только на начальном пустом
  состоянии. Добавлен переход «опубликованный old → изменённое дерево →
  load без публикации → reload».
- Weak gap: поиск caption возвращал один элемент и не доказывал канонический
  порядок нескольких совпадений. Добавлен набор, где directory и caption
  order намеренно расходятся с name order.
- Weak gap: прежние identity-утверждения не замечали промежуточную вторую
  публикацию. Добавлен узкий observer числа замен; это единственный
  white-box элемент, потому что транзиентную публикацию нельзя надёжно
  наблюдать только конечным identity.
- Brittle audit: конкурентные оракулы используют `Event` и `Barrier`, не
  используют `sleep` и не считают тайминг доказательством. Все thread/future
  завершаются с timeout-стражами, а исключения читателей извлекаются через
  `Future.result()`.
- Happy-only audit: success reload сопоставлен с broken/missing/empty;
  casefold-match — с empty/no-match; immutable current snapshot — со старой
  ссылкой после смены файлов; одиночный reload — с конкурентным.
- Повторный прогон двух concurrency-тестов выполнен 25 раз подряд: все
  50 test invocations прошли.

## Изменения

- `tests/test_registry_snapshot.py`: добавлены три риск-ориентированных
  теста и точные loaded/skipped assertions идемпотентного reload:
  непубликующий `load()` после изменения дерева, ровно одна публикация на
  reload, канонический порядок нескольких Unicode casefold-совпадений.
- `docs/handoff/e1-s3-qa-tester.md`: записан этот QA gate.

## Команды и результаты

- Baseline `uv run --no-sync pytest -q -rs` → 197 passed, 4 skipped,
  coverage 89,21 %.
- Focus после QA-изменений:
  `uv run --no-sync pytest -q tests/test_registry_snapshot.py --no-cov`
  → 14 passed.
- Повтор concurrency focus
  `pytest -k "simultaneous_reloads or four_readers"` в цикле 25 раз
  → 25/25 успешно.
- Пять in-memory mutation runs завершились ожидаемыми тестовыми отказами:
  publish-from-load, double publish, lower вместо casefold, caption sort и
  reload без lock.
- Финальный `uv run --no-sync pytest -q -rs` → 200 passed, 4 skipped,
  coverage 89,21 %, threshold 85 % достигнут.
- `uv run --no-sync ruff check .` → успешно.
- `uv run --no-sync ruff format --check .` → успешно, 115 файлов.
- `uv run --no-sync mypy src tests` → успешно, 41 source file.
- `uv run --no-sync lint-imports` → 1 контракт соблюдён.
- `uv run --no-sync ruff check --select C90 --config
  "lint.mccabe.max-complexity=3" src/skillhub/registry` → успешно.
- `git diff --check` и `git diff --check df21067..58e2750` → успешно.

## Findings

P0: 0.
P1: 0.
P2: 0.
P3: 0.

**GATE_OK**
