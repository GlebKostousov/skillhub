# Выжимка

Роль: security
Scope: `df21067..58e2750`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **НУЖНА_ДОРАБОТКА**
Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Штатный файловый путь строит новый словарь целиком до одного присваивания,
закрывает его `MappingProxyType` и публикует только frozen `Skill`. Один
`Lock` охватывает загрузку и публикацию только для `reload()`; `snapshot`,
`search()` и отдельный `load()` блокировку не берут. Исключение до присваивания
оставляет прежний снимок, а context manager освобождает lock. Query не
логируется.

Gate блокирует публично достижимый `_loader`. Он вызывается до файловой
границы S2 и без проверки возвращённого отчёта. Через
`SkillRegistry(root, _loader=...)` можно опубликовать невалидные, повторные или
изменяемые объекты: dictionary comprehension молча применяет last-wins для
повторного имени, а mapping proxy защищает только сам словарь, не его
произвольные значения. Та же callable может удерживать lock неограниченно или
войти в `reload()` повторно и взаимно заблокировать поток.

# Отчёт

## Договор, канон и delta

- Договор E1-S3 требует неизменяемый copy-on-write snapshot, одно присваивание
  после полной загрузки, один lock только для конкурирующих reload, lock-free
  readers, четыре параллельных читателя и стабильный case-insensitive поиск
  (`docs/phases/orchestrator-e1.md:163-205`).
- Атакующий инвариант договора отдельно запрещает обход файловой политики S2
  конкурентным вызовом (`docs/phases/orchestrator-e1.md:187-195`). Канон
  определяет снимок как неизменяемый `Mapping[str, Skill]`, заменяемый
  `reload()` одним присваиванием (`CONTEXT.md:11-14`).
- Файловый канон S2 требует единственную семантическую границу
  `RegistryFilesystem`, bounded read, safe YAML, held-root containment,
  first-wins для повторного канонического имени и безопасные issue/log
  (`docs/architecture/skillhub.md:250-288`,
  `docs/architecture/decision-rationale.md:120-147`,
  `docs/security/threat-model.md:31-51`).
- Диапазон содержит ровно один commit
  `58e2750 feat(registry): добавить атомарный снимок`. Он меняет
  `_models.py`, `_registry.py` и добавляет
  `tests/test_registry_snapshot.py`; HTTP, сеть, зависимости и LLM-путь не
  меняются.

## Фактический поток публикации

1. Конструктор создаёт отдельный пустой dict, оборачивает его в
   `MappingProxyType`, создаёт обычный non-reentrant `Lock` и сохраняет
   необязательный `_loader` (`src/skillhub/registry/_registry.py:32-56`).
2. Штатный `load()` строго разрешает root и использует принятую файловую
   границу S2. Ожидаемые filesystem/parser/limit исключения превращаются в
   безопасный root issue. Но при наличии `_loader` callable вызывается раньше
   этой границы и раньше её exception normalization
   (`src/skillhub/registry/_registry.py:67-80`).
3. `reload()` удерживает один lock во время всей загрузки, построения snapshot
   и публикации. `_snapshot_from_report()` сначала сортирует `report.skills`,
   строит новый dict и mapping proxy; только после успешного завершения
   выполняется одно присваивание `self._snapshot = ...`
   (`src/skillhub/registry/_registry.py:82-91`,
   `src/skillhub/registry/_registry.py:126-134`).
4. `snapshot` возвращает одну текущую ссылку. `search()` сначала сохраняет
   текущую ссылку локально и обходит только её значения, поэтому один вызов не
   смешивает два опубликованных словаря и не берёт reload lock. Результат —
   новый tuple (`src/skillhub/registry/_registry.py:58-65`,
   `src/skillhub/registry/_registry.py:93-110`).
5. Штатный отчёт содержит tuple, а `Skill`, `LoadIssue` и `LoadReport`
   объявлены frozen/slots. Значения `Skill` состоят из строк и bool, поэтому
   обычный S2-путь не оставляет изменяемого вложенного alias
   (`src/skillhub/registry/_models.py:6-66`,
   `src/skillhub/registry/_registry.py:112-123`,
   `src/skillhub/registry/_registry.py:158-181`).

## Finding

### P2 — Публичный `_loader` обходит S2 и допускает неканонический изменяемый snapshot

Идентификатор: `SEC-E1S3-001`.

**Достижимость.** Публичный пакет экспортирует `SkillRegistry`, а его
публичный конструктор принимает keyword `_loader` и без capability/type
границы сохраняет callable (`src/skillhub/registry/__init__.py:1-13`,
`src/skillhub/registry/_registry.py:32-55`). Это не только внутренний
monkeypatch: committed concurrency tests вызывают
`SkillRegistry(tmp_path, _loader=loader)` непосредственно через публичный
фасад (`tests/test_registry_snapshot.py@58e2750:193-243`,
`tests/test_registry_snapshot.py@58e2750:245-319`).

**Обход политики.** Ветка `_loader` немедленно возвращает переданный отчёт и
не выполняет strict root resolve, `RegistryFilesystem`, byte/tree budgets,
UTF-8/YAML/metadata validation, containment, safe issue construction или
first-wins duplicate check (`src/skillhub/registry/_registry.py:67-80`).
Публикация затем доверяет каждому элементу `report.skills`: проверяет только
доступ к `.name`, сортирует и строит dict
(`src/skillhub/registry/_registry.py:126-134`). Аннотации dataclass не являются
runtime-валидацией (`src/skillhub/registry/_models.py:6-66`).

Отсюда следуют три наблюдаемых класса нарушения одного trust-boundary:

1. Два элемента с одинаковым `.name` не дают безопасный `duplicate_name` и не
   сохраняют S2 first-wins: более позднее значение молча перезаписывает первое
   в dictionary comprehension. Это позволяет подменить уже принятое имя.
2. Объект с невалидными `name`, `caption`, `description`, пустым или
   произвольным `body` публикуется без файлового происхождения и проверок S2.
3. Произвольный mutable объект с атрибутами `name`/`caption` принимается вместо
   `Skill`. `MappingProxyType` запрещает изменение ключей, но изменение такого
   объекта после `reload()` немедленно видно старым и новым читателям. Поэтому
   заявленная глубокая неизменяемость snapshot зависит от добросовестности
   внедрённой callable.

**Concurrency/DoS.** Callable выполняется под non-reentrant reload lock. Она
может не вернуть управление, а замыкание, повторно вызвавшее `reload()` того
же объекта, взаимно блокирует поток. Все последующие reload образуют очередь;
lock-free readers продолжают видеть старый снимок. Исключение callable
выходит наружу, но присваивание не выполняется, а `with` освобождает lock —
частичный снимок при этом не публикуется
(`src/skillhub/registry/_registry.py:82-91`).

**Влияние и приоритет.** Нарушается прямой инвариант E1-S3 «конкурентный вызов
не обходит S2»: недоверенные metadata/body могут стать каноническим состоянием,
повторное имя — заменить первое, а значение — меняться после публикации.
Приоритет P2, а не P1: в текущем слайсе нет HTTP/конфигурационного пути к
параметру; атакующий должен контролировать локальную Python-сборку объекта.
Тем не менее seam находится в production-классе и публичной сигнатуре, поэтому
это не только недостаток теста.

**Требуемое закрытие.** Удалить `_loader` из production-конструктора и
перенести детерминированное управление тестами в test-only/private harness,
который не является входом публичного фасада. Если альтернативный источник
должен остаться продуктовой возможностью, его результат обязан пройти одну
публикационную границу: exact/immutable модели, все инварианты полей S2,
уникальность с явной принятой политикой и defensive freezing/copy до
присваивания. Произвольную callable нельзя считать безопасной работой под
reload lock.

## Нормальный путь: atomicity, exceptions, roots и replay

- Новый dict и mapping proxy полностью создаются до присваивания. Ошибка
  загрузки, сортировки, чтения атрибута или построения dict оставляет текущую
  ссылку неизменной; context manager lock освобождается. На штатном пути
  частичного или изменяемого alias не найдено
  (`src/skillhub/registry/_registry.py:82-91`,
  `src/skillhub/registry/_registry.py:126-134`).
- Один сломанный каталог становится issue, а валидные соседи публикуются;
  тест фиксирует пять loaded и один skipped
  (`tests/test_registry_snapshot.py@58e2750:170-190`). Это сохраняет изоляцию
  S2.
- Отсутствующий или нечитаемый root штатно превращается в пустой отчёт с
  безопасным root issue, после чего `reload()` публикует пустой snapshot.
  Пустой существующий root публикует пустой snapshot без issue
  (`src/skillhub/registry/_registry.py:76-80`,
  `src/skillhub/registry/_registry.py:198-223`,
  `tests/test_registry_snapshot.py@58e2750:322-345`). Это потенциально очищает
  ранее непустое состояние, но прямо соответствует принятому договору
  (`docs/phases/orchestrator-e1.md:190-195`).
- Обычный `load()` не берёт lock и не публикует состояние; несколько таких
  вызовов могут читать диск параллельно. Только `reload()` сериализован, как
  требует договор (`src/skillhub/registry/_registry.py:67-91`,
  `tests/test_registry_snapshot.py@58e2750:114-123`).
- У штатного reload нет автоматического retry. Поэтому библиотека не
  переигрывает I/O сама, а последовательные публикации следуют порядку входа
  в lock. Повтор неизменённого дерева создаёт новую mapping-ссылку, но
  сохраняет равные значения и порядок
  (`tests/test_registry_snapshot.py@58e2750:348-364`). Версия/anti-replay
  token для локального side-effect-free фасада договором не требуется.
  `_loader` может вернуть устаревший отчёт и тем самым обойти эту гарантию —
  это часть `SEC-E1S3-001`.
- Четыре читателя и последовательность reload проверены без блокировки
  readers; допустимы только два целых непустых набора
  (`tests/test_registry_snapshot.py@58e2750:245-319`). Тест использует
  корректный injected loader и потому не доказывает безопасность самого seam.

## A–I

### A. Input validation

Штатный путь сохраняет S2: bounded bytes/tree, strict UTF-8, safe YAML,
metadata/body validation и duplicate first-wins выполняются до `Skill`.
Публичный `_loader` пропускает их все, а `_snapshot_from_report()` повторно не
валидирует отчёт — `SEC-E1S3-001`
(`src/skillhub/registry/_registry.py:67-80`,
`src/skillhub/registry/_registry.py:126-134`).

### B. Authz / tenant

N/A: пользователей, tenants и прикладной authorization surface нет. Локальная
граница полномочий alternative loader рассмотрена в finding.

### C. Secrets / logs

`search()` не вызывает logger и не передаёт query в диагностические поля
(`src/skillhub/registry/_registry.py:93-110`). Штатные root/skill issues
по-прежнему логируют только стабильный code и safe identifier, без body,
абсолютного пути и traceback
(`src/skillhub/registry/_registry.py:189-223`). Исключение `_loader` здесь не
логируется, но seam может вне файловой политики опубликовать произвольный body
— `SEC-E1S3-001`.

### D. Injection / files

Изменения штатного S2 loader не ослабляют held-root containment, no-follow,
safe YAML или budgets. Но `_loader` выбирается до этого пути и является прямым
product-code bypass файловой security boundary — `SEC-E1S3-001`.

### E. Crypto

N/A: криптографического протокола, ключей, подписей и токенов в delta нет.
SHA-256 остаётся только безопасным идентификатором hostile имени каталога
(`src/skillhub/registry/_registry.py:189-196`).

### F. HTTP / network

N/A: endpoint, request schema, cookie, CORS, CSRF и сетевой клиент в delta
отсутствуют. Защита будущего изменяющего HTTP reload относится к E1-S4; текущая
поверхность — локальный Python API.

### G. Replay / integrity

Штатно lock охватывает load и одно присваивание; readers держат одну snapshot
ссылку, а повторная загрузка неизменного дерева сохраняет канонический порядок.
Старые ссылки не меняются после новой публикации
(`tests/test_registry_snapshot.py@58e2750:93-111`,
`tests/test_registry_snapshot.py@58e2750:348-364`). `_loader` допускает stale
replay, last-wins duplicate substitution и post-publication mutation —
`SEC-E1S3-001`.

### H. DoS / bounds

Штатная файловая загрузка наследует лимиты S2, reload не имеет retry, а
читатели не ждут I/O lock. Несколько reload сериализуются, но ожидающие потоки
не coalesce и не имеют отдельного timeout; без HTTP/untrusted caller это не
самостоятельный finding. Произвольная `_loader` callable может навсегда
удержать lock или re-enter его — DoS-аспект `SEC-E1S3-001`.

### I. Stored XSS / data exposure

HTML/DOM sink в слайсе отсутствует; query и metadata не логируются поиском.
Штатные `Skill` глубоко неизменяемы для своих полей. Держатель старого
snapshot может и после reload читать ранее полученный body: это ожидаемая
семантика copy-on-write, не новая утечка и не механизм отзыва доступа.
`_loader` способен опубликовать непроверенные metadata/body и mutable object —
`SEC-E1S3-001`.

### J. LLM

N/A: LLM-scope **НЕТ**. Prompt, model output, tool/agent execution и
динамическое исполнение отсутствуют; `Skill` только хранит каталогизированные
данные (`docs/phases/orchestrator-e1.md:163-205`).

## Attack-test gaps

1. Нет стража, что production/public `SkillRegistry` не принимает `_loader`;
   напротив, оба concurrency tests зависят от этого входа
   (`tests/test_registry_snapshot.py@58e2750:193-319`).
2. Нет malicious-report oracle: два одинаковых имени, невалидные поля,
   повторное имя с отличающимся body и проверка, что last-wins не может попасть
   в snapshot.
3. Нет mutable-report/value oracle: list вместо tuple, duck-typed mutable
   `Skill`, изменение caption/body после публикации и наблюдение старым
   snapshot/search.
4. Нет oracle исключения и повторного входа injected loader: прежний snapshot
   должен сохраниться, lock после исключения — освободиться, а reentrant путь —
   не зависнуть. Удаление production seam устраняет необходимость доверять
   такому коду под lock.
5. Concurrent-reader oracle использует два заранее корректных отчёта и
   управляемые события; он не сочетает реальную S2 filesystem load с
   удалением/поломкой root во время reload
   (`tests/test_registry_snapshot.py@58e2750:245-345`).
6. Нет отдельного доказательства отсутствия query в captured logs и поведения
   на очень длинном/Unicode query. В реализации logger отсутствует; HTTP-bound
   и пользовательский предел query относятся к следующему слайсу.
7. Нет прогона конкурентного oracle на experimental free-threaded CPython
   3.13. Канон и текущие тесты подразумевают обычный поддерживаемый runtime;
   включение free-threaded сборки потребует отдельного memory/publication
   доказательства.

## Проверка диапазона

- `git merge-base df21067 58e2750` → `df21067`; диапазон содержит один commit.
- `git diff --check df21067..58e2750` → успешно.
- Тесты и quality-команды в рамках handoff не запускались; оценены committed
  implementation и committed attack-oracles.
- В рамках этого handoff записан только
  `docs/handoff/e1-s3-security.md`; commit не создавался. Параллельные
  незакоммиченные изменения рабочего дерева не использовались как
  доказательство для целевого commit.

Итого: P0 — 0, P1 — 0, P2 — 1, P3 — 0. AppSec gate не пройден.
