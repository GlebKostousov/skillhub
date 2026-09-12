# Выжимка

Роль: security
Scope: `e986218..4cd71a6`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

`SEC-E1S3-001` закрыт. Production-конструктор больше не принимает callable,
`reload()` не использует виртуально переопределяемый публичный `load()` и
получает отчёт только из приватного штатного S2-пути. Этот путь выполняет
strict root resolve, проходит через `RegistryFilesystem`, bounded parse,
валидацию и first-wins duplicate policy до построения снимка.

Неизменяемость, одно присваивание, один reload-lock и lock-free readers
сохранены. Произвольная callable больше не может удержать lock или повторно
войти в `reload()` через production API. `snapshot`, `search()`, query-flow и
безопасное логирование delta не меняет.

# Отчёт

## Договор, исходный finding и delta

- E1-S3 требует неизменяемый copy-on-write snapshot, полную сборку до одного
  присваивания, один lock только для reload, lock-free readers и стабильный
  case-insensitive поиск. Атакующий инвариант отдельно запрещает обход S2
  конкурентным вызовом (`docs/phases/orchestrator-e1.md:163-205`;
  `CONTEXT.md:11-14`).
- S2 считает root доверенной конфигурацией, а каталог — недоверенным вводом.
  Единственная файловая граница обязана выполнять containment, бюджеты,
  безопасный YAML, строгую валидацию и duplicate first-wins
  (`docs/security/threat-model.md:31-51`;
  `docs/architecture/skillhub.md:250-288`).
- Исходный `SEC-E1S3-001` был P2: `_loader` в публично достижимом конструкторе
  мог вернуть произвольный, повторный или изменяемый отчёт в обход S2, а также
  зависнуть или re-enter `reload()` под non-reentrant lock
  (`docs/handoff/e1-s3-security.md:78-137`).
- Диапазон содержит один commit
  `4cd71a6 fix(registry): закрыть обход загрузчика`. Изменены только
  `src/skillhub/registry/_registry.py` и
  `tests/test_registry_snapshot.py`; HTTP, сеть, зависимости, query/log
  helpers, модели и LLM-путь не менялись.

## Фактический security-flow

1. `SkillRegistry(root)` сохраняет только root, обычный `Lock` и отдельный
   пустой `MappingProxyType`; callable/loader-параметра и `_loader` slot больше
   нет (`src/skillhub/registry/_registry.py:32-49`).
2. Публичный `load()` не публикует состояние и делегирует приватной
   `_load_registry(root)`. `reload()` под единственным lock вызывает эту
   функцию **напрямую**, а не `self.load()`, поэтому override публичного метода
   не участвует в публикации (`src/skillhub/registry/_registry.py:51-77`).
3. `_load_registry()` строго разрешает root, открывает принятую
   `RegistryFilesystem`-границу и только затем получает `_load_entries()`.
   Ожидаемые filesystem/parser/limit ошибки нормализуются в безопасный root
   issue (`src/skillhub/registry/_registry.py:99-106`).
4. `RegistryFilesystem.list_candidates()` ограничивает число root entries и
   задаёт стабильный порядок. Каждый кандидат читается с остаточным общим
   byte budget через удерживаемый файловый контекст
   (`src/skillhub/registry/_filesystem.py:68-105`;
   `src/skillhub/registry/_registry.py:108-118`).
5. До создания `Skill` выполняются strict UTF-8, safe YAML, запрет YAML
   graph/tag, проверка kebab-case/длины имени, description, caption и непустого
   body (`src/skillhub/registry/_document.py:18-126`;
   `src/skillhub/registry/_registry.py:153-176`).
6. Уже принятое каноническое имя находится в `state.names`; повторный кандидат
   становится `duplicate_name`, не добавляется в `skills`, а первый каталог в
   стабильном порядке остаётся победителем
   (`src/skillhub/registry/_registry.py:130-151`;
   `src/skillhub/registry/_registry.py:178-181`;
   `tests/test_registry.py@4cd71a6:769-789`).
7. Штатный отчёт содержит tuple проверенных frozen `Skill`. Новый словарь и
   mapping proxy строятся до публикации, после чего `reload()` заменяет
   `_snapshot` одним присваиванием
   (`src/skillhub/registry/_models.py:6-66`;
   `src/skillhub/registry/_registry.py:73-77`;
   `src/skillhub/registry/_registry.py:121-128`).
8. `snapshot` возвращает текущую ссылку, а `search()` один раз захватывает её
   локально и обходит без reload-lock. Query сравнивается через `casefold()` и
   никуда не логируется (`src/skillhub/registry/_registry.py:51-58`;
   `src/skillhub/registry/_registry.py:79-96`).

## Закрытие `SEC-E1S3-001`

### Constructor callable

Сигнатура теперь ровно `SkillRegistry(root: Path)`, `_loader` удалён из
сигнатуры, slots, состояния и импорта `Callable`
(`src/skillhub/registry/_registry.py:3-8`;
`src/skillhub/registry/_registry.py:38-49`). Публичный фасад экспортирует
`SkillRegistry`, но не приватную `_load_registry`
(`src/skillhub/registry/__init__.py:1-13`). Regression-oracle фиксирует
`TypeError` для прежнего `_loader=...`
(`tests/test_registry_snapshot.py@4cd71a6:138-145`).

### Override публичного `load()`

`reload()` вызывает модульную `_load_registry(self._root)` напрямую. Подкласс
может вернуть из `load()` неканонический отчёт, но этот отчёт не публикуется и
reload всё равно читает валидный файл через S2
(`src/skillhub/registry/_registry.py:60-77`;
`tests/test_registry_snapshot.py@4cd71a6:101-124`;
`tests/test_registry_snapshot.py@4cd71a6:179-198`).

### Только доверенный S2 report

На production-пути у `_snapshot_from_report()` один caller — `reload()`, а
полученный им report создаётся `_load_entries()` после файловой границы и
валидации. Публичный `load()` остаётся read-only относительно snapshot.
Модульная monkeypatch приватной функции используется только тестами
конкурентности (`tests/test_registry_snapshot.py@4cd71a6:339-468`). Это
возможность произвольного кода уже внутри процесса, который также способен
заменить методы или приватное состояние; она не является входом публичного
фасада и не входит в принятую модель атакующего.

### Immutable и duplicate policy

`LoadReport.skills/issues` — tuple, модели `Skill`, `LoadIssue` и `LoadReport`
— frozen/slots, их поля состоят из неизменяемых примитивов. Snapshot оборачивает
новый внутренний dict без внешнего mutable alias. Duplicate last-wins в
dictionary comprehension недостижим для штатного отчёта: S2 раньше отклоняет
повтор и сохраняет первый валидный каталог
(`src/skillhub/registry/_models.py:6-66`;
`src/skillhub/registry/_registry.py:108-128`;
`src/skillhub/registry/_registry.py:153-181`).

### Lock, reentrancy и DoS

Lock по-прежнему охватывает полную загрузку, построение и одно присваивание
только в `reload()`. Readers и отдельный `load()` его не берут. После удаления
callable и обхода виртуального `load()` нет нового production callback,
способного re-enter `reload()` или удерживать lock неограниченным
пользовательским кодом (`src/skillhub/registry/_registry.py:60-96`).

Штатный файловый I/O может ждать ОС, а параллельные reload стоят в очереди,
но объём обхода и разбора ограничен S2. В слайсе нет HTTP/untrusted reload
caller, retry или нового удалённого источника, поэтому самостоятельного
P0–P3 finding здесь нет.

## A–I

### A. Input validation

Публикация получает только `Skill`, прошедшие S2: bounded bytes/tree, strict
UTF-8, safe YAML, metadata/body validation и duplicate first-wins. Удалён
единственный штатный путь передачи непроверенного отчёта
(`src/skillhub/registry/_registry.py:99-181`;
`src/skillhub/registry/_document.py:18-126`).

### B. Authz / tenant

N/A: users, tenants и authorization surface в delta отсутствуют. Root остаётся
доверенной локальной конфигурацией по модели угроз
(`docs/security/threat-model.md:31-34`).

### C. Secrets / logs

`search()` не вызывает logger и не передаёт query в диагностику. Root/skill
отказы по-прежнему публикуют только stable reason и safe identifier, без body,
опасного исходного имени, абсолютного пути и traceback
(`src/skillhub/registry/_registry.py:79-96`;
`src/skillhub/registry/_registry.py:183-219`;
`docs/security/threat-model.md:50-52`).

### D. Injection / files

Reload больше не обходит `RegistryFilesystem`. Containment/no-follow,
ограниченное чтение, safe YAML и запрет динамического исполнения S2 не
менялись. Нового file/path/YAML injection route в delta нет.

### E. Crypto

N/A: криптографического протокола, ключей, подписей и токенов нет. SHA-256
остаётся только безопасным идентификатором hostile имени
(`src/skillhub/registry/_registry.py:183-190`).

### F. HTTP / network

N/A: endpoint, cookies, CORS, CSRF и сетевой клиент отсутствуют. Доверенный
UNC-root и запрет внешних целей принадлежат уже принятой S2 filesystem
границе; delta их не меняет.

### G. Replay / integrity

Каждый reload сериализован, получает полный проверенный отчёт, строит новый
mapping до публикации и заменяет одну ссылку. Старые ссылки остаются целыми;
первое каноническое имя не может быть подменено повтором. Автоматического
retry/stale external loader больше нет
(`src/skillhub/registry/_registry.py:68-77`;
`src/skillhub/registry/_registry.py:108-128`).

### H. DoS / bounds

Произвольная constructor callable устранена, virtual callback под lock
устранён. Файловый путь наследует root-entry, candidate, byte, auxiliary-tree
и link-resolution budgets S2. Readers не ждут reload-lock; отдельный `load()`
тоже остаётся вне lock. Очередь одновременных reload не coalesce и не имеет
timeout, но в текущем локальном API это неблокирующий операционный остаток, а
не подтверждённая уязвимость.

### I. Stored XSS / data exposure

HTML/DOM sink в слайсе отсутствует. Metadata/body остаются данными, query не
логируется. Опубликованные `Skill` и mapping неизменяемы; сохранение старой
snapshot-ссылки после reload — ожидаемая COW-семантика, не механизм отзыва
доступа (`CONTEXT.md:11-14`;
`src/skillhub/registry/_models.py:6-66`).

### J. LLM

N/A: LLM-scope **НЕТ**. Prompt dispatch, model output, tool/agent execution и
динамическое исполнение в delta отсутствуют; `SKILL.md` только валидируется и
каталогизируется (`docs/phases/orchestrator-e1.md:163-205`).

## Attack-test gaps

Ниже только неблокирующие пробелы доказательной базы; подтверждённой
уязвимости за ними не найдено.

1. Concurrency-oracles подменяют приватную `_load_registry` корректными
   отчётами для детерминированного управления потоками. Нет отдельного
   production oracle, доказывающего недостижимость arbitrary report через все
   формы monkeypatch/import-hook; такой код уже обладает произвольным
   выполнением внутри процесса и находится вне модели атакующего.
2. Нет единого многопоточного oracle, который совмещает настоящую S2
   filesystem load с конкурентной заменой/поломкой root. Отдельные S2
   containment/race oracles и S3 whole-snapshot oracles покрывают границы
   независимо.
3. Нет captured-log oracle отсутствия очень длинного или Unicode query.
   Реализация поиска вообще не обращается к logger, а query-bound будущего
   HTTP API относится к E1-S4.
4. Нет проверки fairness/coalescing большого числа ожидающих reload и timeout
   зависшего OS/filesystem I/O. Удалённый callable DoS закрыт; внешнего
   вызывающего reload в этом слайсе нет.
5. Нет отдельного конкурентного прогона на experimental free-threaded CPython.
   Текущий договор и committed oracles относятся к поддерживаемому runtime.

## Проверка диапазона

- `git merge-base e986218 4cd71a6` → `e986218`.
- В диапазоне один commit:
  `4cd71a6 fix(registry): закрыть обход загрузчика`.
- `git diff --check e986218..4cd71a6` → успешно.
- По условию re-gate код и тесты не изменялись и тесты не запускались;
  проверены committed implementation, S2 security contract и committed
  attack-oracles.
- Записан только `docs/handoff/e1-s3-rework-1-security.md`; commit не
  создавался.
- Во время финальной проверки в worktree появились параллельные незакоммиченные
  изменения `tests/test_registry_snapshot.py` и чужие rework-1 handoff-файлы.
  Они не создавались этим security review и не использовались как
  доказательство целевого commit-range.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 0. **GATE_OK**
