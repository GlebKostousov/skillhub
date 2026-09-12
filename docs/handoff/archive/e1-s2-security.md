# Выжимка

Рецепт: да.

- Проверен diff `dd6d135..20748bc`, договор E1-S2, контекст, архитектура, модель угроз, реализация и тесты.
- AppSec-scope: **ДА**. LLM-scope: **НЕТ**: в слайсе нет вызовов модели, агентов, инструментов или использования модельного ответа; `SKILL.md` только читается как данные (`src/skillhub/registry/_registry.py:1-11`, `docs/phases/orchestrator-e1.md:107-145`).
- Найдено: **1 × P1, 2 × P2, 0 × P0/P3**.
- Gate не пройден: containment уязвим к TOCTOU, отдельные безопасно загружаемые YAML обрывают всю загрузку, а обход `has_files` не ограничен и на Windows заходит во внешние junction.
- Полный прогон: `111 passed, 3 skipped`, branch coverage `92.51%`. Все три пропуска — symlink-тесты Windows с ошибкой `1314`; поэтому они не являются доказательством обязательного containment.

# Отчёт

## Фактический поток данных

`SkillRegistry.load()` разрешает корень, материализует и сортирует непосредственные записи, разрешает каталог и проверяет его относительно корня (`src/skillhub/registry/_registry.py:50-102`). Затем `SKILL.md` разрешается и проверяется, из файла читается не более 65 537 байт, лимит применяется до строгого UTF-8 и YAML (`src/skillhub/registry/_registry.py:105-147`). Front matter сначала сканируется с запретом alias/anchor/tag, затем проходит `yaml.safe_load`; поля валидируются, после чего рекурсивно вычисляется `has_files` (`src/skillhub/registry/_registry.py:150-235`). Успех и ошибки возвращаются frozen-моделями и tuple (`src/skillhub/registry/_models.py:6-48`); ожидаемый отказ превращается в стабильный `LoadIssue` и warning (`src/skillhub/registry/_registry.py:251-261`).

## Findings

### P1 — Между containment-проверкой и чтением остаётся эксплуатируемый TOCTOU

**Атака.** Код получает разрешённый путь `SKILL.md` и проверяет его относительно корня, но открывает этот путь позже отдельной операцией (`src/skillhub/registry/_registry.py:125-146`). Между `resolve()` и `open()` родительский каталог можно заменить symlink/junction. Проверка относится к старому объекту, а открытие — уже к новой внешней цели.

На текущем Windows-хосте детерминированный runtime-probe заменил каталог на junction ровно перед `_read_bounded()`. Реестр вернул внешний валидный файл как `Skill(name="outside-after-check", body="OUTSIDE_PRIVATE_BODY")`. Значит, внешнее содержимое не только открывается, но и попадает в публичный `LoadReport`, нарушая критерий «внешнее содержимое не читается» (`docs/phases/orchestrator-e1.md:123-137`).

**Почему тесты не закрывают риск.** Независимый от ОС тест проверяет лишь случай, когда `resolve()` сразу вернул внешний путь (`tests/test_registry.py:385-408` в `20748bc`). Реальные тесты внутреннего/внешнего symlink и внешнего файла `has_files` находятся в `tests/test_registry.py:354-423`, но все три пропущены на Windows через helper `tests/test_registry.py:65-76`. Ни один тест не меняет объект после успешного `resolve()`.

**Исправление.** Открывать файл через привязанный к корню/каталогу handle без следования reparse points и до чтения проверять конечную цель именно открытого handle. Повторный `Path.resolve()` не устраняет race. Нужен детерминированный тест замены родителя после проверки и до чтения; внешний body не должен быть открыт или возвращён.

### P2 — `yaml.safe_load` может выбросить непойманный `ValueError` и сорвать весь реестр

**Атака.** Обёртка YAML ловит только `RecursionError` и `yaml.YAMLError` (`src/skillhub/registry/_registry.py:159-169`), а цикл каталогов изолирует только `_InvalidSkillError` (`src/skillhub/registry/_registry.py:80-102`). Безопасные встроенные конструкторы PyYAML способны выбрасывать обычный `ValueError`: например, implicit timestamp `2026-99-99` и целое из 5000 цифр.

Runtime-probe с одним валидным каталогом и одним таким `SKILL.md` завершил `SkillRegistry.load()` исключением `ValueError: month must be in 1..12`; `LoadReport`, warning и валидный скилл не были возвращены. Это нарушает основной договор изоляции одного дефекта (`docs/phases/orchestrator-e1.md:123-137`) и при прямом выводе исключения допускает traceback с абсолютными путями вместо безопасной причины.

**Исправление.** Нормализовать все ожидаемые исключения конструкторов данных, как минимум `ValueError`/`OverflowError`, в `invalid_yaml`, не ловя `BaseException`/`MemoryError`. Добавить оба payload и соседний валидный каталог в атакующий тест.

### P2 — `has_files` заходит во внешние Windows junction и не имеет бюджета обхода

`os.walk(..., followlinks=False)` используется без лимита глубины, числа каталогов или времени (`src/skillhub/registry/_registry.py:213-235`). На Windows это не блокирует directory junction: runtime-probe показал обход `[".", "references"]`, где `references` был junction на внешний каталог. Конечный внешний файл правильно не был засчитан благодаря `resolve`/containment, но внешнее дерево уже было пройдено.

Большое или циклическое внешнее дерево позволяет удерживать загрузку обходом диска; тот же DoS возможен через большое дерево пустых каталогов внутри скилла. Дополнительно корневой `sorted(root.iterdir())` материализует неограниченное число записей (`src/skillhub/registry/_registry.py:70-79`). Размер `SKILL.md` эту поверхность не ограничивает.

**Исправление.** Делать собственный bounded-обход через `lstat`/reparse-point detection, не входить в symlink и junction, ограничить число посещённых записей/глубину и возвращать стабильную причину превышения. Нужны Windows-junction, cycle/large-empty-tree и budget-тесты.

## A. Input validation

- Положительно: чтение ограничено `MAX_SKILL_FILE_BYTES + 1`, а проверка размера выполняется до decode/parse (`src/skillhub/registry/_registry.py:13-15`, `src/skillhub/registry/_registry.py:105-147`).
- Положительно: UTF-8 декодируется с `errors="strict"` (`src/skillhub/registry/_registry.py:106-112`).
- Положительно: итоговое имя — ASCII kebab-case и не более 64 символов; fallback имени каталога проходит ту же проверку (`src/skillhub/registry/_registry.py:179-188`). Description обязателен и ограничен 1024 символами (`src/skillhub/registry/_registry.py:191-201`), body после trim обязан быть непустым (`src/skillhub/registry/_registry.py:150-169`).
- Не пройдено: data-originated `ValueError` не превращается в локальный отказ — finding P2.

## B. Authz / tenant

N/A: слайс предоставляет локальный файловый фасад без пользователя, tenant-id и прикладных операций авторизации. Разграничение ОС не заменяет containment, поэтому файловые дефекты учтены в D/H.

## C. Secrets / logs

- Ожидаемые отказы несут только `path` и стабильный `reason`; warning содержит только `event`, `error_code`, `skill_path` (`src/skillhub/registry/_registry.py:251-257`).
- Каноническое имя скилла исключает управляющие/path-символы, а небезопасное имя каталога заменяется на `unsafe-<sha256-prefix>` (`src/skillhub/registry/_registry.py:19-22`, `src/skillhub/registry/_registry.py:238-245`). `skill_path` — единственное новое разрешённое диагностическое поле (`src/skillhub/core/_redaction.py:15-28`).
- Тест ожидаемого отказа проверяет отсутствие body, marker, абсолютного пути и traceback (`tests/test_registry.py:426-457` в `20748bc`).
- Гарантия неполна: TOCTOU возвращает внешний body, а непойманный YAML-дефект обходит безопасный warning — findings P1/P2.

## D. Injection / files

- Unsafe YAML tags не исполняются: scanner запрещает `AliasToken`, `AnchorToken`, `TagToken`, затем используется `yaml.safe_load` (`src/skillhub/registry/_registry.py:10-22`, `src/skillhub/registry/_registry.py:159-169`). Атакующий Python-tag тест подтверждает отсутствие side effect (`tests/test_registry.py:209-228` в `20748bc`).
- Статический resolved containment присутствует для корня, каталога, `SKILL.md` и вложенных файлов (`src/skillhub/registry/_registry.py:57-68`, `src/skillhub/registry/_registry.py:85-99`, `src/skillhub/registry/_registry.py:125-146`, `src/skillhub/registry/_registry.py:226-235`).
- Не пройдено: проверка не привязана к открываемому handle; Windows junction обходится в `has_files` — findings P1/P2.

## E. Crypto

N/A: криптографических операций, ключей, подписей, токенов и шифрования в diff нет. SHA-256 используется только как необратимый безопасный идентификатор недоверенного имени пути, не как security protocol (`src/skillhub/registry/_registry.py:238-245`).

## F. HTTP

N/A: diff не добавляет endpoint, request schema, cookie, CORS, CSRF или сетевой клиент. Публичная поверхность слайса — Python-фасад `skillhub.registry` (`src/skillhub/registry/__init__.py:1-13`).

## G. Replay / money

Денежных и внешних side effect нет. Для неизменного дерева порядок детерминирован сортировкой и tuple (`src/skillhub/registry/_registry.py:70-102`); повторная загрузка проверена тестом (`tests/test_registry.py:300-319` в `20748bc`). Конкурентная атомарная публикация снимка относится к E1-S3, не к этому diff.

## H. DoS / bounds

- Байтовый предел до decode/parse и запрет YAML graph tokens существенно ограничивают file/YAML bomb (`src/skillhub/registry/_registry.py:13-22`, `src/skillhub/registry/_registry.py:139-169`).
- Deep YAML изолируется только для `RecursionError`; обычные constructor exceptions обрывают всю загрузку — finding P2.
- Число корневых записей и рекурсивный обход дополнительных файлов не ограничены; Windows junction позволяет вынести обход за корень — finding P2.

## I. Stored XSS / data exposure

В diff нет HTML/DOM/HTTP sink; caption, description и body остаются недоверенными строками и должны контекстно кодироваться будущим слоем отображения. Frozen-модели и tuple не дают изменить опубликованный результат (`src/skillhub/registry/_models.py:6-48`, `tests/test_registry.py:79-104`). Фактическая data exposure присутствует через TOCTOU внешнего `SKILL.md` — finding P1.

## J. LLM

N/A: E1-S2 не вызывает модель, не строит model prompt, не принимает model output и не даёт содержимому файлов инструментов/исполнения. Поэтому prompt injection, tool authorization и output validation здесь не применимы; граница слайса — только недоверенный локальный файл (`docs/phases/orchestrator-e1.md:107-145`, `src/skillhub/registry/_registry.py:1-11`).

## Attack-test gaps

1. TOCTOU root/parent/file swap после успешного `resolve()` и до `open()`, с проверкой содержимого открытого handle.
2. Windows directory junction в `has_files`; три symlink-теста `20748bc` фактически не исполняются на целевой ОС.
3. Implicit invalid timestamp, integer > Python digit limit и соседний валидный скилл.
4. Ограничение числа root entries, глубины/числа посещённых каталогов, циклическое и большое пустое дерево.
5. Hostile path cases с `\n`, `\r`, ANSI/Unicode control и максимально длинным именем в warning/report; текущий тест покрывает только пробел/`=` и marker (`tests/test_registry.py:426-457` в `20748bc`).
6. Permission/race branches `root_unreadable`, `file_unreadable`, `directory_unreadable`; покрытие самого loader в полном прогоне — 84%.

## Dependency freshness

`pyyaml>=6.0.3` добавлен как runtime dependency (`pyproject.toml:6-14`), а lock фиксирует PyYAML `6.0.3` с хэшами артефактов (`uv.lock:489-505`). На 2026-09-11 это актуальный стабильный релиз от 2025-09-25; для 6.0.3 не обнаружены прямые известные уязвимости: <https://github.com/yaml/pyyaml/releases/tag/6.0.3>, <https://security.snyk.io/package/pip/pyyaml/6.0.3>. Использование `safe_load` корректно, но не отменяет finding по необработанным constructor exceptions.
