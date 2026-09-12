# Выжимка

Роль: security
Scope: `dd817f1..8a5eabf`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Rework закрывает прежние блокеры SoT и flash без ослабления S1–S3. Живой
каталог читается только из `SkillRegistry.capture()`: публичный router больше
не принимает отдельный `initial_report` и не хранит второе HTTP-поколение.
Прямой `registry.reload()` обновляет API и UI вместе.

Flash резервируется до scan, публикует только `SkillSummary` и человеческие
issue, живёт 300 с монотонного TTL и атомарно погашается первым точным GET.
Ёмкость восьми слотов восстанавливается без вытеснения свежего redirect.
CSPRNG-marker остаётся 32 байта / 43 ASCII, теперь с потолком восьми попыток.

Занятый или переполненный POST больше не отдаёт JSON 409 в HTML-форму: после
CSRF/Origin приходит 303 на фиксированный `notice=reload-unavailable`. Сырой
query не попадает в context. Jinja autoescape, CSP, Host/Origin, CORS off и
localhost bind сохранены.

# Отчёт

Рецепт: да

## Договор, предыдущие находки и delta

- E1-S4 требует единый поиск API/UI, защищённый reload, безопасный partial
  result и текстовое отображение hostile metadata при сохранении CSP,
  TrustedHost, CORS off и localhost bind
  (`docs/phases/orchestrator-e1.md:219-260`).
- Предыдущий security re-gate `6da2a10..0da3ae5` дал GATE_OK и оставил
  неблокирующие пробелы: orphan flash без TTL, удержание полного `LoadReport`
  в памяти, отсутствие wall-time и `no-store` на обычной CSRF-странице
  (`docs/handoff/e1-s4-rework-2-security.md:369-412`).
- Reviewer того же диапазона оставил P2 `E1-S4-R2-REV-001` (два авторитета
  `registry + initial_report`) и P2 `E1-S4-R2-REV-002` (вечная заполненность
  flash и тела инструкций в слотах)
  (`docs/handoff/e1-s4-rework-2-reviewer.md:23-63`).
- В `dd817f1..8a5eabf` один commit:
  `8a5eabf fix(web): закрыть замечания третьего rework E1-S4`; merge-base
  равен `dd817f15d237b6a75d2d22833d60e37708f29d63`.
- Delta меняет 11 файлов: registry capture как одно поколение, web catalog
  без собственного live snapshot, TTL/public-only flash, 303 notice вместо
  HTML JSON 409 и committed oracles. Runtime-зависимости и lock-файл не
  менялись.
- `git diff --check dd817f1..8a5eabf` успешен. По условию re-gate тесты не
  запускались; проверены committed implementation и committed attack-oracles.

## Один SoT snapshot: registry vs HTTP

1. `SkillRegistry` публикует одно неизменяемое `_generation`: `RegistryCapture`
   связывает `report` и `snapshot` одним присваиванием. Readers берут целую
   ссылку без writer-lock
   (`src/skillhub/registry/_registry.py:32-41`;
   `src/skillhub/registry/_registry.py:73-95`;
   `src/skillhub/registry/_registry.py:147-151`).
2. `create_router()` больше не принимает `initial_report`. `_CatalogState` не
   хранит `_current` и не вызывает `registry.reload()` сам. Live API и HTML
   каждый раз проецируют `self._registry.capture()`
   (`src/skillhub/app_factory.py:39-64`;
   `src/skillhub/web/routes.py:44-71`;
   `src/skillhub/web/_catalog.py:99-108`;
   `src/skillhub/web/_catalog.py:203-208`).
3. `public_catalog()` копирует только `name/caption/description/has_files` и
   человеческий `ReloadSummary`. Поиск HTML/API фильтрует уже построенный
   публичный список тем же name/caption casefold, что и `_search_skills()`
   (`src/skillhub/web/_catalog.py:75-79`;
   `src/skillhub/web/_catalog.py:225-226`;
   `src/skillhub/registry/_models.py:81-87`).
4. Прямой `registry.reload()` на публичном router seam теперь обновляет и
   `/api/skills`, и `/skills`. Рассинхрон «реестр новый, HTTP старый» из
   `E1-S4-R2-REV-001` закрыт
   (`tests/test_e1_s4_rework_2_qa.py:182-265`;
   `tests/test_skills_catalog.py:374-411`).
5. `app.state.skill_registry` по-прежнему отсутствует
   (`tests/test_e1_s4_rework_2_qa.py:182-204`).

Подтверждённого второго live-владельца HTTP-каталога нет. Flash — одноразовая
публичная копия уже опубликованного отчёта, а не параллельный SoT.

## Flash TTL без тел инструкций

- Резервация маркера выполняется до filesystem scan. При 8 занятых слотах,
  чужой reservation или исчерпании 8 CSPRNG-попыток POST не читает дерево
  (`src/skillhub/web/_catalog.py:110-121`;
   `src/skillhub/web/_catalog.py:195-200`;
   `src/skillhub/web/routes.py:105-108`).
- `publish_reload()` сразу строит `_PublicCatalog` и сохраняет только его.
  `LoadReport` / `Skill.body` в `_flashes` не удерживаются; DTO больше не
  несёт сырые `path/reason`
  (`src/skillhub/web/_catalog.py:123-138`;
   `src/skillhub/web/_catalog.py:229-246`;
   `tests/test_e1_s4_rework_3.py:106-119`).
- TTL 300 с монотонных часов. Purge идёт под тем же lock на reserve / publish
  / consume. Восемь брошенных redirect истекают и освобождают admission, не
  вытесняя ещё живой слот
  (`src/skillhub/web/_catalog.py:17-18`;
   `src/skillhub/web/_catalog.py:219-222`;
   `tests/test_e1_s4_rework_3.py:122-190`).
- Отмена или ошибка до publish вызывает `discard_reservation()`; flash не
  создаётся, gate освобождается в `finally`
  (`src/skillhub/web/routes.py:105-117`).

`E1-S4-R2-REV-002` и пункт public-only retention из
`docs/handoff/e1-s4-rework-2-security.md:414-418` закрыты.

## Marker: entropy, bound, consume, replay

- Marker создаётся `token_urlsafe(32)`: 256 бит CSPRNG и 43 URL-safe ASCII.
  Вход принимается только `[A-Za-z0-9_-]{43}` через `fullmatch` ASCII.
  Raw marker не попадает в context, HTML или log
  (`src/skillhub/web/_catalog.py:19-21`;
   `src/skillhub/web/_catalog.py:146-153`;
   `src/skillhub/web/routes.py:118-121`).
- Collision-loop ограничен `_FLASH_MARKER_ATTEMPTS = 8`. Исчерпание даёт
  безопасный отказ до scan и отпускает gate
  (`src/skillhub/web/_catalog.py:195-200`;
   `tests/test_e1_s4_rework_3.py:235-299`).
- Consume — `dict.pop()` под lock после purge. Повтор, miss, expired и
  malformed не показывают flash и не ставят `reload_performed`
  (`src/skillhub/web/_catalog.py:146-153`;
   `src/skillhub/web/routes.py:81-88`).
- Любой GET с присутствующим `reload` или `notice` получает
  `Cache-Control: no-store`
  (`src/skillhub/web/routes.py:91-92`).

## CSRF / same-origin и 409 JSON только API

- Origin, Host, Content-Type, bounded body и CSRF проверяются до admission.
  Невалидный POST не берёт gate и не резервирует marker
  (`src/skillhub/web/routes.py:95-99`;
   `src/skillhub/web/_reload_guard.py:46-62`).
- Same-origin: ровно один Origin и Host, canonical
  `scheme/hostname/normalized-port`, запрет null/userinfo/path/query/fragment
  (`src/skillhub/web/_reload_guard.py:65-111`).
- Request-token кодируется в ASCII до `compare_digest`; Unicode даёт 403, не
  500 (`src/skillhub/web/_reload_guard.py:177-183`).
- Занятость, полная ёмкость и collision-fail больше не поднимают
  `ReloadUnavailableError` в HTML-ответ. Browser POST получает 303 на
  `/skills?notice=reload-unavailable`. JSON-обработчик 409 остаётся только
  для непойманного `SkillHubError` и в HTML-мутации не используется
  (`src/skillhub/web/routes.py:98-108`;
   `src/skillhub/web/routes.py:185-196`;
   `src/skillhub/web/errors.py:35-40`;
   `src/skillhub/web/errors.py:95-104`).
- 303 Location содержит только относительный путь и server-generated marker
  либо фиксированный notice. CSRF, body, skill path в URL нет.

## Notice query не отражает произвольный ввод

- `notice` сравнивается с константой `reload-unavailable`. В шаблон уходит
  только bool `reload_unavailable`. Сырое значение не копируется в context
  (`src/skillhub/web/routes.py:24`;
   `src/skillhub/web/routes.py:78-88`;
   `src/skillhub/web/_catalog.py:54-66`).
- Alert рисует фиксированные русские строки и статическую ссылку `/skills`.
  `{{ notice }}` в шаблоне нет
  (`src/skillhub/web/templates/skills.html:21-38`).
- JS удаляет `reload`/`notice` через `URLSearchParams` и `history.replaceState`;
  sink — `textContent`, не `innerHTML`
  (`src/skillhub/web/static/app.js:3-37`).
- Oversized `q` не отражается: error-страница подставляет пустой `query` и
  фиксированный `query_error`
  (`src/skillhub/web/_catalog.py:165-177`).

Подтверждённого reflected XSS или open redirect через `notice` нет.

## XSS escape и сохранение S1–S3

### XSS / assets

- Jinja autoescape; `safe`/`Markup` отсутствуют. Metadata, query, issue
  message/action и hidden CSRF идут через `{{ ... }}`
  (`src/skillhub/web/templates/skills.html:14-18`;
   `src/skillhub/web/templates/skills.html:73-76`;
   `src/skillhub/web/templates/skills.html:99`;
   `src/skillhub/web/templates/skills.html:110`;
   `src/skillhub/web/templates/skills.html:136-141`).
- Единственный package JS: фиксированные строки и `textContent`. Запрещённые
  sink отсутствуют (`src/skillhub/web/static/app.js:1-37`;
   `tests/test_e1_s4_qa.py:852-870`).
- CSP `default-src 'self'; object-src 'none'; base-uri 'none';
  frame-ancestors 'none'`; nosniff, XFO DENY, Referrer-Policy same-origin
  (`src/skillhub/web/security.py:11-30`).

### Host / HTTP

- Middleware order не менялся:
  `ProcessErrorBoundary → SecurityHeaders → StrictHost → TrustedHost → route`
  (`src/skillhub/app_factory.py:43-52`).
- StrictHost: ровно один ASCII Host, точное локальное имя, decimal port
  `1..65535` (`src/skillhub/web/_host_guard.py:17-70`).
- Launcher — `127.0.0.1`, access log off, CORS middleware нет
  (`src/skillhub/_server.py:100-108`).

### S1

- Safe envelopes, request-id, unexpected boundary не менялись. Новый HTML
  notice проходит те же security headers
  (`src/skillhub/web/_boundaries.py:21-55`;
   `src/skillhub/web/errors.py:59-71`).

### S2

- Containment, no-follow, UTF-8, safe YAML, budgets не менялись. HTTP не
  принимает root/path/body. Flash строится из уже проверенного `LoadReport`
  (`src/skillhub/web/routes.py:111-113`;
   `src/skillhub/web/_catalog.py:123-128`).

### S3

- Non-reentrant writer lock, copy-on-write generation, lock-free readers
  сохранены. `capture()` возвращает тот же объект, что публикует `reload()`
  (`src/skillhub/registry/_registry.py:85-95`).

## A–J

### A. Input validation

Строго проверяются Host, Origin, Content-Type, declared/actual body, form
grammar, configured CSRF и request token. Query ограничен 128 символами до
поиска. Marker — exact ASCII 43. `notice` — equality к одной константе,
иначе игнорируется. Обхода через Unicode token, duplicate fields или
malformed marker не найдено.

### B. Authz / tenant

N/A для users, tenants и RBAC: локальное unauthenticated приложение.
CSRF-token защищает browser mutation и не аутентифицирует процесс.
Configured root не выбирается из HTTP. Прямой `SkillRegistry.reload()` на
router seam — in-process composition, не HTTP-обход Origin/CSRF.

### C. Secrets / logs

Production CSRF — `token_urlsafe(32)`, только в памяти routes и hidden
field. Marker — 256 бит, одноразовый доступ к публичной проекции. Query,
Host, form, skill body, raw notice и absolute root не пишутся прикладным
logger. 403/413/415 логируют только code/status
(`src/skillhub/web/errors.py:95-122`).

### D. Injection / files

Host/Origin/form/marker/notice не используются как путь, SQL, shell или code.
HTTP не принимает skills root/path/body. S2 containment сохранена. Jinja и
JS не создают executable sink. Issue path проходит S2 identifier и
`str.format` с именованным полем, затем autoescape
(`src/skillhub/web/_issue_messages.py:81-103`).

### E. Crypto

Оба production token — `secrets.token_urlsafe(32)`. CSRF сравнивается
`compare_digest` по ASCII bytes. Marker — непрозрачное 256-битное значение
с capped collision retry. Собственной криптографии, подписи, KDF нет.

### F. HTTP / CORS / CSP

Bind `127.0.0.1`; Host allowlist точный; CORS отсутствует. Origin сравнивает
scheme/casefold-host/normalized-port с уже проверенным Host. CSP/XFO/nosniff
охватывают catalog, notice и 303 follow-up. Proxy trust остаётся у внешнего
ASGI runner.

### G. Replay / money

Денежных операций нет. Первый точный GET атомарно потребляет marker; replay
и TTL-expiry не показывают flash. Capacity не вытесняет живой redirect.
HTTP-reload single-flight: пересекающийся валидный POST сразу 303 notice и
не занимает worker. Registry generation публикуется целиком.

### H. DoS / bounds

Host decimal parse, query, marker grammar, reload body, form fields,
flash-map (8), marker attempts (8) и TTL (300 с) bounded. Busy POST не ждёт
и не занимает worker. Один admitted reload наследует S2 budgets. OS/filesystem
syscall по-прежнему без wall-time; HTTP amplification нет.

### I. XSS / assets

Metadata/query/issues autoescape; notice не отражается; client — `textContent`;
CSP запрещает object/base/framing. HTTP-проекция не содержит `Skill.body`.
Static assets — package-owned `/static`, skill root не mounted.

### J. LLM

N/A: LLM-scope **НЕТ**. В диапазоне нет model call, prompt dispatch,
tool/agent execution, обработки model output или динамического исполнения
`SKILL.md`; файлы только валидируются и каталогизируются как данные
(`docs/phases/orchestrator-e1.md:228-239`).

## Attack-test gaps

Ниже неблокирующие пробелы доказательной базы; подтверждённой уязвимости за
ними не найдено.

1. Нет отдельного oracle `GET /skills?notice=<script>` / duplicate `notice`.
   Код сравнивает с константой и не кладёт raw query в context, но
   HTML-assertion на отсутствие отражения не закреплена
   (`src/skillhub/web/routes.py:78-88`;
   `src/skillhub/web/templates/skills.html:21-38`).
2. Cancellation oracle не покрывает Uvicorn disconnect после
   `publish_reload()` и до отправки 303. Orphan flash теперь истекает за
   300 с и не держит body, но send-failure cleanup нет
   (`src/skillhub/web/routes.py:105-121`;
   `src/skillhub/web/_catalog.py:17-18`).
3. Нет oracle shutdown / зависшего OS syscall во время admitted reload.
   `run_in_threadpool()` не бросает начатую работу; wall-time нет
   (`src/skillhub/web/routes.py:105-117`).
4. `_PublicCatalog.search` дублирует predicate `_search_skills` без общего
   helper. Сейчас фильтры совпадают и вход уже отсортирован; drift-oracle
   кроме текущего name-order теста нет
   (`src/skillhub/web/_catalog.py:75-79`;
   `src/skillhub/registry/_models.py:81-87`;
   `tests/test_skills_catalog.py:374-411`).
5. CSPRNG test подменяет генератор. Нет statistical/randomness oracle;
   production использует `secrets`, пространство 256 бит
   (`src/skillhub/web/_catalog.py:195-200`;
   `tests/test_e1_s4_rework_3.py:235-299`).
6. Публичный configured-CSRF seam по-прежнему допускает URL-safe токен длины
   1. Production всегда даёт 32 случайных байта
   (`src/skillhub/web/_reload_guard.py:15-43`;
   `src/skillhub/app_factory.py:41`).
7. Обычный `/skills` с hidden CSRF не получает `Cache-Control: no-store`;
   marker/notice страницы получают. Cross-origin disclosure не подтверждён
   (`src/skillhub/web/routes.py:91-92`;
   `src/skillhub/web/templates/skills.html:110`).
8. `?notice=reload-unavailable` без предшествующего POST показывает тот же
   фиксированный alert. Это UX-spoof same-origin ссылки, не отражение и не
   мутация; отдельного anti-spoof oracle нет
   (`src/skillhub/web/routes.py:88`).
9. Origin/Host matrix, raw-socket Uvicorn/h11 и HTTP/2 в этом re-gate не
   перегонялись. Изменяющих WebSocket routes нет
   (`src/skillhub/web/_reload_guard.py:65-157`;
   `src/skillhub/web/_host_guard.py:17-70`).
10. Полный S1–S3 suite, browser test, dependency audit и secret scan не
    запускались по условию задачи. Вывод основан на committed delta,
    неизменённых принятых границах и committed oracles.

## Проверка диапазона

- `git rev-parse dd817f1` →
  `dd817f15d237b6a75d2d22833d60e37708f29d63`.
- `git rev-parse 8a5eabf` →
  `8a5eabf435cf68cd6fda65fb4a00ff0dc5366892`.
- `git merge-base dd817f1 8a5eabf` →
  `dd817f15d237b6a75d2d22833d60e37708f29d63`.
- Диапазон содержит один commit:
  `8a5eabf fix(web): закрыть замечания третьего rework E1-S4`.
- `git diff --check dd817f1..8a5eabf` → успешно.
- До записи handoff worktree был чист.
- Код и тесты не изменялись и не запускались; записан только
  `docs/handoff/e1-s4-rework-3-security.md`; commit не создавался.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 0. **GATE_OK**
