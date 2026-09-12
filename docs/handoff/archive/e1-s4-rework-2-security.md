# Выжимка

Роль: security
Scope: `6da2a10..0da3ae5`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **GATE_OK**
Findings: 0 — P0: 0, P1: 0, P2: 0, P3: 0.

Rework закрывает прежние AppSec-блокеры без ослабления S1–S3. Конкурентный
reload теперь получает неблокирующий admission: один запрос выполняет scan, а
пересекающийся валидный POST немедленно получает безопасный 409 и не занимает
worker token. При отмене уже начатый синхронный scan завершается, но
cancel-check не сохраняет недоступный клиенту flash; gate освобождается в
`finally`.

PRG-marker теперь создаётся CSPRNG из 32 случайных байт, принимается только в
точном 43-символьном URL-safe ASCII-формате, хранится в bounded map и атомарно
погашается первым GET. Заполненная ёмкость отклоняет новый reload до scan,
поэтому уже выданный redirect не вытесняется. Marker-страница получает
`Cache-Control: no-store`; повтор, неизвестное и malformed значение не
показывают flash и не отражают вход.

Configured CSRF проверяется при сборке router; Unicode request-token безопасно
отклоняется до `compare_digest`. `SkillRegistry` удалён из `app.state`, поэтому
production HTTP-каталог больше нельзя рассинхронизировать через публичный
state-handle. Строгий Host, canonical Origin, CORS off, Jinja autoescape,
локальный JavaScript, CSP и принятые S1–S3 границы сохранены.

# Отчёт

## Договор, предыдущие находки и delta

- E1-S4 требует единый поиск API/UI, защищённый reload, безопасный partial
  result и текстовое отображение hostile metadata при сохранении CSP,
  TrustedHost, CORS off и localhost bind
  (`docs/phases/orchestrator-e1.md:219-260`).
- Предыдущий security re-gate оставил P3
  `SEC-E1S4-R1-001`: последовательный marker был угадываемым и replayable,
  хранился вместе с полным старым поколением и не погашался GET
  (`docs/handoff/e1-s4-rework-1-security.md:220-261`).
- QA независимо подтвердил повторный flash, reviewer — очередь вместо
  single-flight, потерю раннего redirect при eviction, расхождение authority
  через `app.state`, неканонический порядок, неявный контракт configured CSRF
  и обход web-фасада
  (`docs/handoff/e1-s4-rework-1-qa-tester.md:83-113`;
  `docs/handoff/e1-s4-rework-1-reviewer.md:15-83`).
- В `6da2a10..0da3ae5` один commit:
  `0da3ae5 fix(web): close E1-S4 rework findings`; merge-base равен
  `6da2a10efa2a4d6d10da30af5b1b25a96de3a3f5`.
- Delta меняет 11 файлов: registry search ordering, web facade/composition,
  catalog flash state, reload request/admission, safe 409 и committed
  regression-oracles. Runtime-зависимости и lock-файл не менялись.
- `git diff --check 6da2a10..0da3ae5` успешен. По условию re-gate тесты не
  запускались; проверены committed implementation и committed attack-oracles.

## Single-flight admission, busy и cancellation

1. Reload полностью проверяет Origin, Content-Type, bounded body и CSRF до
   admission. Невалидный запрос не получает gate и не достигает файловой
   загрузки (`src/skillhub/web/routes.py:83-89`;
   `src/skillhub/web/_reload_guard.py:46-62`).
2. Per-app AnyIO lock берётся через синхронный `acquire_nowait()`. Если один
   reload уже владеет gate, `WouldBlock` сразу преобразуется в фиксированный
   `ReloadUnavailableError` 409; waiter и очередь scans не создаются
   (`src/skillhub/web/routes.py:83-92`;
   `src/skillhub/web/errors.py:34-40`).
3. Только admitted request вызывает `run_in_threadpool()`. Поэтому
   пересекающийся POST не занимает общий worker token, а на экземпляр routes
   одновременно выполняется не более одного HTTP-triggered filesystem scan
   (`src/skillhub/web/routes.py:90-97`).
4. После завершения синхронной загрузки выполняется явный cancellation
   checkpoint. Если caller уже отменён, новое текущее поколение остаётся
   атомарно опубликованным реестром, но marker не добавляется в outstanding
   flashes; `finally` освобождает admission
   (`src/skillhub/web/routes.py:90-97`;
   `src/skillhub/web/_catalog.py:104-116`).
5. При обычном завершении generation сначала полностью построен и опубликован,
   затем запоминается для 303. Между проверкой capacity и сохранением нет
   второго admitted POST; конкурентный GET может только уменьшить занятость
   flash-map (`src/skillhub/web/routes.py:90-100`;
   `src/skillhub/web/_catalog.py:113-130`).
6. Committed oracles удерживают первый scan, требуют мгновенный 409 и ровно
   один loader call у пересекающегося POST, подтверждают доступность общего
   worker pool и проверяют освобождение admission/flash-capacity после отмены
   активного caller
   (`tests/test_skills_catalog.py:543-669`;
   `tests/test_skills_catalog.py:671-750`).

Подтверждённого queue amplification, повторного scan из одного burst, утечки
gate или worker-pool starvation в новом HTTP-пути нет.

## Random one-time marker, capacity, pop и replay

- Marker создаётся `secrets.token_urlsafe(32)`: 256 бит исходной
  CSPRNG-энтропии и 43 URL-safe ASCII-символа без padding. Сервер повторяет
  генерацию при совпадении с одним из outstanding marker
  (`src/skillhub/web/_catalog.py:15-17`;
  `src/skillhub/web/_catalog.py:169-173`).
- Входной `reload` допускается только при полном совпадении с anchored ASCII
  grammar `[A-Za-z0-9_-]{43}`. Raw input не попадает в context, HTML, log или
  redirect (`src/skillhub/web/_catalog.py:123-130`;
  `src/skillhub/web/routes.py:64-81`).
- Flash-map и текущее поколение защищены одним `threading.Lock`. Точный
  marker извлекается через `dict.pop()` под lock, поэтому из двух
  конкурентных GET только один получает связанное поколение и
  `reload_performed=True`; второй видит текущее поколение без ложного статуса
  (`src/skillhub/web/_catalog.py:90-130`).
- Повторный, неизвестный, уже погашенный или malformed marker не запускает
  reload и не восстанавливает старый flash. Server-side consume не зависит от
  выполнения `history.replaceState`
  (`src/skillhub/web/_catalog.py:123-130`;
  `src/skillhub/web/static/app.js:3-13`).
- Outstanding storage ограничен восемью элементами. При полном map новый POST
  получает 409 **до** `SkillRegistry.reload()`, поэтому существующий redirect
  не вытесняется и не подменяется текущим поколением
  (`src/skillhub/web/_catalog.py:15-17`;
  `src/skillhub/web/_catalog.py:118-121`;
  `src/skillhub/web/routes.py:90-95`).
- Любой GET с присутствующим параметром `reload`, включая malformed и miss,
  получает `Cache-Control: no-store`. Это не позволяет HTTP cache повторно
  выдать уже погашенный server-side flash
  (`src/skillhub/web/routes.py:64-81`).
- Committed tests фиксируют CSPRNG shape/miss/non-reflection/no-store,
  конкурентный consume ровно один раз, capacity 8 с отказом девятого до scan
  и точное старое поколение при более новой публикации
  (`tests/test_skills_catalog.py:961-1088`).

`SEC-E1S4-R1-001` и `QA-E1S4-R1-001` закрыты.

## Marker и секреты

- 303 Location содержит только относительный путь и server-generated marker.
  CSRF-token, query, skill body, issue body и filesystem path в URL не
  включаются (`src/skillhub/web/routes.py:98-101`;
  `tests/test_skills_catalog.py:885-958`).
- CSRF-token создаётся отдельно через `secrets.token_urlsafe(32)`, живёт в
  приватном объекте routes и попадает только в hidden field HTML. Cookie,
  URL, browser storage и прикладное логирование не используются
  (`src/skillhub/app_factory.py:39-64`;
  `src/skillhub/web/routes.py:33-46`;
  `src/skillhub/web/templates/skills.html:89-94`).
- HTTP-проекция generation содержит только
  `name/caption/description/has_files` и безопасные issue summaries. Поле
  `Skill.body` не копируется в `SkillSummary` и не выводится шаблоном
  (`src/skillhub/web/_catalog.py:20-28`;
  `src/skillhub/web/_catalog.py:176-210`;
  `src/skillhub/web/templates/skills.html:111-130`).
- В process memory flash всё ещё удерживает immutable `LoadReport`, включая
  body, пока marker не потреблён. Это bounded retention максимум восьми
  отчётов, а не HTTP disclosure: marker lookup выдаёт только строго
  типизированный page context, абсолютного пути и raw broken body в report
  нет (`src/skillhub/web/_catalog.py:66-76`;
  `src/skillhub/web/_catalog.py:90-130`;
  `src/skillhub/registry/_models.py:6-49`).
- 409/403/413/415 и framework errors используют фиксированные code/message;
  logger получает только code/status, без body, token, marker или Host
  (`src/skillhub/web/errors.py:18-122`).

Подтверждённой утечки CSRF-token, тела скилла, абсолютного пути или
attacker-controlled значения через marker, response либо log нет.

## Configured CSRF и Unicode request

- Публичный `create_router()` валидирует configured token один раз до
  регистрации routes. Контракт явно допускает 1–256 символов из URL-safe ASCII
  alphabet и fail-fast выдаёт `ValueError` для пустой строки, Unicode,
  whitespace/прочих символов и превышения длины
  (`src/skillhub/web/_reload_guard.py:15-43`;
  `src/skillhub/web/routes.py:104-127`).
- Request-path независимо требует strict UTF-8 form, ровно одну пару с точным
  именем `csrf_token`, не допускает duplicate/extra fields и ограничивает
  parser максимум двумя полями
  (`src/skillhub/web/_reload_guard.py:132-174`).
- Submitted и expected token кодируются в ASCII до сравнения. Percent-encoded
  Unicode или иной не-ASCII ввод даёт контролируемый false/403, а не
  `TypeError`/500; равные ASCII bytes сравниваются через
  `secrets.compare_digest`
  (`src/skillhub/web/_reload_guard.py:177-183`).
- Production composition не использует нижнюю границу внешнего seam:
  `create_app()` всегда создаёт 32 случайных байта CSPRNG
  (`src/skillhub/app_factory.py:39-42`).
- Committed oracles принимают короткий и 256-символьный URL-safe configured
  token, отклоняют invalid configuration при сборке и отдельно требуют 403
  для Unicode request-token
  (`tests/test_skills_catalog.py:500-540`;
  `tests/test_e1_s4_qa.py:717-745`).

Прежний Unicode 500 закрыт; silent misconfiguration публичного router seam
устранена.

## App authority и канонический поиск

- `create_app()` создаёт ровно один registry и один initial report, передаёт
  их закрытому объекту routes и больше не публикует mutable authority через
  `app.state` (`src/skillhub/app_factory.py:39-68`).
- HTTP readers захватывают одну immutable `_CatalogGeneration`; reload
  строит report и summary до одного присваивания `_current`
  (`src/skillhub/web/_catalog.py:66-76`;
  `src/skillhub/web/_catalog.py:92-111`;
  `src/skillhub/web/routes.py:56-81`).
- `LoadReport.search()` и `SkillRegistry.search()` используют одну
  `_search_skills()`, которая теперь сортирует вход по canonical
  `Skill.name`. Имя каталога больше не может изменить наблюдаемый порядок web
  относительно registry facade
  (`src/skillhub/registry/_models.py:69-87`;
  `src/skillhub/registry/_registry.py:80-94`).
- Новый contract test подтверждает отсутствие `app.state.skill_registry`, а
  отдельный oracle сверяет report, registry, API и HTML при несовпадении имени
  каталога и frontmatter name
  (`tests/test_e1_s4_qa.py:170-191`;
  `tests/test_skills_catalog.py:374-410`).
- `StrictHostMiddleware` экспортирован через `skillhub.web`, и composition
  root больше не зависит от приватной раскладки web
  (`src/skillhub/web/__init__.py:1-19`;
  `src/skillhub/app_factory.py:12-20`;
  `tests/test_repository_contract.py:189-205`).

В production composition подтверждённого второго mutable владельца текущего
HTTP-каталога нет.

## Host, Origin, XSS и сохранение S1–S3

### Host / Origin / HTTP

- Фактический middleware order остаётся
  `ProcessErrorBoundary → SecurityHeaders → StrictHost → TrustedHost → route`.
  StrictHost требует ровно один raw ASCII Host, точное локальное имя и
  необязательный decimal port `1..65535` до split-based TrustedHost
  (`src/skillhub/app_factory.py:43-52`;
  `src/skillhub/web/_host_guard.py:17-70`).
- Reload повторно требует ровно один Origin и Host, canonical
  `scheme/hostname/normalized-port`, запрещает null, whitespace, userinfo,
  path, query и fragment и сравнивает с фактическим ASGI scheme и уже
  проверенным Host (`src/skillhub/web/_reload_guard.py:65-120`).
- Content-Type должен быть единственным urlencoded form; declared и actual
  body независимо ограничены 4096 байтами до form parse
  (`src/skillhub/web/_reload_guard.py:123-157`).
- Поддерживаемый launcher остаётся на `127.0.0.1` без access log; CORS
  middleware отсутствует. Host/Origin ошибки не отражают исходные заголовки
  (`src/skillhub/_server.py:100-108`;
  `src/skillhub/web/_host_guard.py:29-38`;
  `src/skillhub/web/errors.py:50-122`).

### XSS / browser surface

- Metadata, query, issue text и hidden token проходят Jinja autoescape;
  `safe`/`Markup`, inline script и HTML-building sink в шаблоне отсутствуют
  (`src/skillhub/web/templates/skills.html:13-18`;
  `src/skillhub/web/templates/skills.html:53-60`;
  `src/skillhub/web/templates/skills.html:69-94`;
  `src/skillhub/web/templates/skills.html:111-130`).
- Единственный локальный JavaScript использует фиксированные строки и
  `textContent`; `innerHTML`, `document.write`, eval и dynamic script URL
  отсутствуют (`src/skillhub/web/static/app.js:1-28`;
  `tests/test_e1_s4_qa.py:852-871`).
- CSP остаётся `default-src 'self'; object-src 'none'; base-uri 'none';
  frame-ancestors 'none'`; также сохранены `nosniff`, XFO DENY,
  `Referrer-Policy: same-origin` и server-generated request ID
  (`src/skillhub/web/security.py:11-30`).

### S1

- Safe startup, loopback bind, bounded logging, safe HTTP envelopes,
  request-context cleanup и unexpected-error boundary в delta не менялись
  (`docs/handoff/e1-s1-rework-4-security.md:13-26`;
  `src/skillhub/web/_boundaries.py:21-55`;
  `src/skillhub/web/_boundaries.py:107-148`).
- Новый 409 является обычным `SkillHubError` с фиксированными публичными
  полями и проходит прежний security-header middleware
  (`src/skillhub/web/errors.py:34-40`;
  `src/skillhub/web/errors.py:86-122`).

### S2

- Filesystem containment/no-follow, root/child handle policy, strict UTF-8,
  safe YAML, byte/tree/link budgets, safe issue identifier и duplicate
  first-wins в delta не менялись. HTTP по-прежнему не принимает root/path/body
  (`docs/handoff/e1-s2-rework-4-security.md:42-88`;
  `src/skillhub/app_factory.py:27-40`).
- Flash и HTML работают только с уже проверенным immutable `LoadReport`;
  повторное чтение пути вне `SkillRegistry.reload()` не добавлено
  (`src/skillhub/web/_catalog.py:92-111`).

### S3

- Production registry constructor, приватный S2 loader, non-reentrant writer
  lock, copy-on-write snapshot, lock-free readers и first-wins policy
  сохранены (`docs/handoff/e1-s3-rework-1-security.md:44-81`;
  `src/skillhub/registry/_registry.py:32-94`).
- Добавленная сортировка является чистой операцией над immutable skills и не
  меняет publication/locking. Web generation остаётся целым старым или новым
  объектом (`src/skillhub/registry/_models.py:69-87`;
  `src/skillhub/web/_catalog.py:92-111`).

## A–I

### A. Input validation

Строго проверяются raw Host, canonical Origin, Content-Type, declared/actual
body, form grammar, configured CSRF и request token. Query ограничен 128
Python-символами до поиска; marker имеет exact ASCII grammar и miss
fail-closed. Обхода через Unicode, duplicate fields или malformed marker не
найдено.

### B. Authz / tenant

N/A для users, tenants и RBAC: это локальное unauthenticated приложение.
CSRF-token защищает browser mutation и не является аутентификацией локального
процесса. Configured root не выбирается из HTTP.

### C. Secrets / logs

Production CSRF-token имеет 256 бит исходной CSPRNG-энтропии, хранится только в
памяти routes и не попадает в URL/log/storage. Marker также имеет 256 бит
энтропии, но даёт только одноразовый доступ к публичной проекции. Query, Host,
form body, skill body, raw reason и absolute root не передаются прикладному
logger.

### D. Injection / files

Host/Origin/form/marker не используются как путь, SQL, shell или code. HTTP не
принимает skills root/path/body. S2 containment, no-follow, safe YAML, strict
UTF-8 и budgets сохранены. Jinja и JavaScript не создают executable sink.

### E. Crypto

Оба production token создаются `secrets.token_urlsafe(32)`. CSRF равенство
проверяется `secrets.compare_digest()` над ASCII bytes; marker используется
как непрозрачное 256-битное значение с collision retry. Собственной
криптографии, подписи, KDF или key storage нет.

### F. HTTP / network

Поддерживаемый bind — `127.0.0.1`; Host allowlist точный; CORS отсутствует.
Origin сравнивает scheme/casefold-host/normalized-port с тем же строгим Host.
Security headers и safe error envelopes охватывают busy, marker miss и Host
rejection. Proxy trust остаётся ответственностью внешнего ASGI runner.

### G. Replay / integrity

Первый точный GET атомарно потребляет marker; replay и miss не показывают
flash и не выполняют мутацию. Capacity не вытесняет выданные redirects.
HTTP-reload single-flight: пересекающийся валидный POST получает 409, а
следующий после release может выполнить отдельную осознанную загрузку.
Registry snapshot и web generation публикуются целыми.

### H. DoS / bounds

Host decimal parse, query, marker, reload body, form fields, файловая загрузка
и flash-map bounded. Busy POST не ждёт и не занимает worker. Один admitted
reload наследует S2 budgets; OS/filesystem syscall по-прежнему не имеет
жёсткого wall-time, но concurrent HTTP amplification устранён.

### I. Stored XSS / data exposure

Metadata/query/issues проходят autoescape, client code использует
`textContent`, CSP запрещает object/base/framing и ограничивает ресурсы self.
HTTP-проекция не содержит `Skill.body` или filesystem path. Старый полный
report временно удерживается в bounded memory, но не имеет raw HTTP sink.

### J. LLM

N/A: LLM-scope **НЕТ**. В диапазоне нет model call, prompt dispatch,
tool/agent execution, обработки model output или динамического исполнения
`SKILL.md`; файлы только валидируются и каталогизируются как данные
(`docs/phases/orchestrator-e1.md:219-260`).

## Attack-test gaps

Ниже неблокирующие пробелы доказательной базы; подтверждённой уязвимости за
ними не найдено.

1. Cancellation oracle покрывает отмену async task во время активного worker,
   но не реальный Uvicorn transport disconnect после `remember_reload()` и до
   отправки 303. Такой момент может оставить orphan flash до знания его URL;
   storage bounded восемью элементами, но TTL/cleanup по send failure нет
   (`src/skillhub/web/routes.py:93-101`;
   `tests/test_skills_catalog.py:671-750`).
2. Нет oracle для shutdown или зависшего OS/filesystem syscall во время
   admitted reload. `run_in_threadpool()` не abandons начатую работу и gate
   освобождается после возврата, но отдельного wall-time timeout нет
   (`src/skillhub/web/routes.py:90-97`).
3. Concurrent consume проверен двумя GET, но нет большого race-soak,
   free-threaded CPython run или fault injection между `pop()` и template
   render. Lock/immutable references делают текущую операцию атомарной
   (`src/skillhub/web/_catalog.py:123-130`;
   `tests/test_skills_catalog.py:993-1013`).
4. CSPRNG test подменяет генератор фиксированным корректным значением. Нет
   практического collision-retry oracle и statistical/randomness test;
   production использует стандартный `secrets`, а пространство — 256 бит
   (`src/skillhub/web/_catalog.py:169-173`;
   `tests/test_skills_catalog.py:961-990`).
5. Внешний configured-CSRF seam допускает URL-safe токен длиной один символ.
   Production всегда использует 32 случайных байта, а browser mutation
   независимо требует exact Origin; отдельного minimum-entropy контракта для
   сторонней composition нет
   (`src/skillhub/web/_reload_guard.py:15-43`;
   `src/skillhub/app_factory.py:39-42`).
6. Нет browser/network oracle для POST-303 cache behavior, BFCache/back-forward
   восстановления уже показанного DOM, полного Referer к same-origin static
   request и исполнения `history.replaceState`. Server replay закрыт
   consume-on-read, marker GET имеет `no-store`, а cross-origin Referer
   запрещён `same-origin`
   (`src/skillhub/web/routes.py:64-101`;
   `src/skillhub/web/security.py:24-30`;
   `src/skillhub/web/static/app.js:3-13`).
7. Обычный `/skills` содержит hidden CSRF-token, но не получает
   `Cache-Control: no-store`; marker-страница получает. Same-origin cache не
   создаёт подтверждённого cross-origin disclosure, а после рестарта token
   меняется, но browser cache behavior отдельно не закреплён
   (`src/skillhub/web/routes.py:64-81`;
   `src/skillhub/web/templates/skills.html:89-94`).
8. Flash-map удерживает до восьми полных `LoadReport`, включая непубличные
   bodies, вместо минимальной публичной проекции. HTTP DTO/template исключают
   body, но отдельного memory-retention/heap-inspection oracle нет
   (`src/skillhub/web/_catalog.py:66-76`;
   `src/skillhub/web/_catalog.py:90-130`).
9. Origin matrix не исчерпывает userinfo/password, path/query/fragment,
   Unicode/NFKC authority, malformed IPv6, duplicate Content-Length и
   настоящий proxy-header path. Общий parser и StrictHost fail-closed для
   этих неоднозначных форм
   (`src/skillhub/web/_reload_guard.py:65-157`;
   `tests/test_e1_s4_qa.py:595-649`).
10. Host matrix не прогонялась raw-socket проверкой реального Uvicorn/h11,
    HTTP/2 adapter и WebSocket scope. Изменяющих WebSocket routes в E1-S4 нет
    (`src/skillhub/web/_host_guard.py:17-70`;
    `tests/test_e1_s4_qa.py:535-714`).
11. Полный S1–S3 regression suite, browser test, dependency audit и secret
    scan в этом re-gate не запускались по прямому условию задачи. Вывод
    основан на точной committed delta, неизменённых принятых границах и
    committed regression-oracles.

## Проверка диапазона

- `git rev-parse 6da2a10` →
  `6da2a10efa2a4d6d10da30af5b1b25a96de3a3f5`.
- `git rev-parse 0da3ae5` →
  `0da3ae56b5c2e4ae2965e74dc9094d08ec818b84`.
- `git merge-base 6da2a10 0da3ae5` →
  `6da2a10efa2a4d6d10da30af5b1b25a96de3a3f5`.
- Диапазон содержит один commit:
  `0da3ae5 fix(web): close E1-S4 rework findings`.
- `git diff --check 6da2a10..0da3ae5` → успешно.
- До записи handoff worktree был чист.
- Код и тесты не изменялись и не запускались; записан только
  `docs/handoff/e1-s4-rework-2-security.md`; commit не создавался.
- Во время финальной проверки появились параллельные untracked handoff
  `e1-s4-rework-2-logic-simple.md` и `e1-s4-rework-2-reviewer.md`. Они не
  создавались security-ролью и не использовались как доказательство gate.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 0. **GATE_OK**
