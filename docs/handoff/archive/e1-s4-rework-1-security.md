# Выжимка

Роль: security
Scope: `11393ba..7fe15c7`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **НУЖНА_ДОРАБОТКА**

Findings: 1 — P0: 0, P1: 0, P2: 0, P3: 1.

Переработка закрывает прежние AppSec-блокеры. До TrustedHost теперь стоит
строгий parser единственного ASCII `Host`, который принимает только три
точных локальных имени и необязательный десятичный порт `1..65535`.
Percent-encoded Unicode, неверная длина и дубликаты CSRF-токена закрываются
контролируемым 403 до reload; Origin сравнивается с тем же уже проверенным
Host по canonical tuple `scheme/host/port`.

Каталог и итог загрузки теперь публикуются одним неизменяемым поколением.
Ожидающие reload остаются вне общего worker pool. Человеческие
issue-сообщения строятся из фиксированной карты и безопасного
S2-идентификатора, после чего проходят Jinja autoescape.

Gate блокирует один P3: последовательный PRG-marker хранит восемь полных
прошлых поколений и не погашается после GET. Любой клиент без Origin/CSRF
может угадать свежий `/skills?reload=N` и повторно показать устаревшие
metadata/issues как результат выполненной перезагрузки. Мутации, тела скиллов
и секреты недоступны; cross-origin страница не может прочитать ответ, поэтому
severity не выше P3.

# Отчёт

## Договор, предыдущие находки и delta

- E1-S4 требует единый каталог/API, защищённый reload, безопасное отображение
  битого шестого каталога и текстовую обработку hostile metadata при
  сохранении CSP, TrustedHost, CORS off и localhost bind
  (`docs/phases/orchestrator-e1.md:219-260`).
- Предыдущий security-gate нашёл P3 `SEC-E1S4-001`: Unicode `csrf_token`
  доходил до строкового `secrets.compare_digest()` и давал `TypeError`/500
  (`docs/handoff/e1-s4-security.md:99-136`).
- QA нашёл P2 `QA-E1S4-001`: Starlette TrustedHost выделял часть до первого
  двоеточия и принимал malformed authority с доверенным префиксом
  (`docs/handoff/e1-s4-qa-tester.md:94-121`).
- Reviewer и logic-simple нашли P2 из-за раздельной публикации registry
  snapshot и HTTP-summary, позволявшей смешать данные и issue разных reload
  (`docs/handoff/e1-s4-reviewer.md:13-31`;
  `docs/handoff/e1-s4-logic-simple.md:17-29`).
- Perf зафиксировал занятие общего worker pool ожидающими синхронную
  reload-блокировку, а UX потребовал человеческие issue-сообщения и
  Post/Redirect/Get
  (`docs/handoff/e1-s4-perf.md:180-214`;
  `docs/handoff/e1-s4-ux-client.md:110-131`).
- В `11393ba..7fe15c7` один commit
  `7fe15c7 fix(web): устранить замечания E1-S4`; merge-base равен
  `11393ba`. Delta вводит `_host_guard.py`, `_catalog.py`,
  `_issue_messages.py`, `_reload_guard.py`, сокращает `routes.py`, переносит
  поиск одного отчёта в `LoadReport`, добавляет PRG и асинхронный admission
  lock. Runtime-зависимости и lock-файл не менялись.
- `git diff --check 11393ba..7fe15c7` успешен. По условию re-gate тесты не
  запускались; проверены committed implementation и committed attack-oracles.

## Host parser и фактический middleware order

- `StrictHostMiddleware` получает raw ASGI headers, case-insensitive
  отбирает все поля `Host` и принимает значение только при количестве ровно
  один. Значение обязано декодироваться как ASCII
  (`src/skillhub/web/_host_guard.py:29-59`).
- Authority проходит anchored `fullmatch`: только `127.0.0.1`, `localhost`
  или test-only `testserver`, затем необязательные `:` и ASCII digits.
  Userinfo, trusted-prefix suffix, path/query/fragment, whitespace,
  percent-encoding, пустой/знаковый/нечисловой порт и лишнее двоеточие не
  соответствуют grammar
  (`src/skillhub/web/_host_guard.py:20-28`,
  `src/skillhub/web/_host_guard.py:41-48`).
- Порт без ведущих нулей после нормализации обязан быть непустым, иметь не
  более пяти значащих цифр и значение не больше 65535. Поэтому `0`, `65536`
  и сколь угодно длинный ненулевой decimal не вызывают unbounded `int()`;
  проверка длины срабатывает раньше
  (`src/skillhub/web/_host_guard.py:62-70`).
- В factory middleware добавлены в порядке TrustedHost, StrictHost,
  SecurityHeaders, ProcessErrorBoundary. С учётом внешнего оборачивания
  последнего добавленного слоя фактический вход:
  `ProcessErrorBoundary → SecurityHeaders → StrictHost → TrustedHost → route`.
  Поэтому malformed/duplicate/missing Host отклоняется строгим parser до
  прежнего split-based TrustedHost и до route, но ответ всё ещё получает
  request ID и security headers, а неожиданное исключение остаётся внутри
  process boundary (`src/skillhub/app_factory.py:42-53`;
  `src/skillhub/web/_boundaries.py:58-104`,
  `src/skillhub/web/_boundaries.py:107-148`;
  `src/skillhub/web/security.py:34-59`).
- Отказ содержит только фиксированные event/code/status и безопасный JSON;
  исходный Host не отражается и не передаётся logger
  (`src/skillhub/web/_host_guard.py:29-38`;
  `src/skillhub/web/errors.py:50-84`).
- Committed oracles покрывают прежние четыре bypass-формы, 5000 цифр,
  недопустимые границы порта, URL-delimiters, duplicate и missing Host, а
  также допустимые точные authority
  (`tests/test_e1_s4_qa.py:531-650`).

`QA-E1S4-001` закрыт. Подтверждённого Host allowlist bypass в HTTP-поверхности
нет.

## Origin и связь с Host

1. Reload сначала требует ровно один `Origin`; Host повторно извлекается как
   ровно один header. Дубликат любого поля закрывает путь
   (`src/skillhub/web/_reload_guard.py:42-52`).
2. Origin обязан быть непустым, не `null`, без Unicode whitespace. `urlsplit`
   допускается только для `http`/`https`; обязательны hostname и пустые
   username/password/path/query/fragment
   (`src/skillhub/web/_reload_guard.py:55-88`).
3. Raw строка обязана в точности равняться `scheme://netloc`, поэтому suffix,
   slash, query, fragment и parser remainder не игнорируются
   (`src/skillhub/web/_reload_guard.py:72-84`).
4. Порт валидируется в `1..65535`, а отсутствующий порт нормализуется к
   80/443. Результат — tuple `(scheme, hostname.casefold(), port)`
   (`src/skillhub/web/_reload_guard.py:86-97`).
5. Expected origin строится из ASGI scheme и raw Host, который уже прошёл
   внешний StrictHost. Затем он проходит тот же canonical parser. Сравнение
   не смешивает `localhost`, `127.0.0.1`, suffix-host, scheme или
   нестандартный порт; явный default port корректно эквивалентен неявному
   (`src/skillhub/web/_reload_guard.py:42-47`;
   `src/skillhub/web/_host_guard.py:41-48`).

Приложение не читает `Forwarded`/`X-Forwarded-Host`; scheme принадлежит ASGI
runner. Поддерживаемая команда остаётся привязана к `127.0.0.1`, CORS
middleware отсутствует. Cross-site HTML form не задаёт proxy headers, а
custom-header fetch требует preflight и не получает CORS-разрешение
(`src/skillhub/_server.py:100-108`;
`tests/test_e1_s4_qa.py:490-511`).

## CSRF: Unicode, длина, дубликаты и leakage

- Новый token по-прежнему создаётся через `secrets.token_urlsafe(32)` один раз
  на app instance, хранится в памяти routes и передаётся только hidden field.
  Cookie, URL и browser storage не используются
  (`src/skillhub/app_factory.py:39-64`;
  `src/skillhub/web/routes.py:31-44`;
  `src/skillhub/web/templates/skills.html:89-94`).
- Origin проверяется до Content-Type и до чтения тела. Допускается ровно один
  `Content-Type` с media type
  `application/x-www-form-urlencoded`
  (`src/skillhub/web/_reload_guard.py:23-39`,
  `src/skillhub/web/_reload_guard.py:100-107`).
- Declared length проверяется до stream; фактический stream независимо
  суммируется и обрывается после 4096 байт. Поэтому отсутствующий или
  неоднозначный `Content-Length` не снимает фактический предел
  (`src/skillhub/web/_reload_guard.py:109-135`).
- Form декодируется strict UTF-8, разбирается strict parser с максимум двумя
  полями и принимается только при одной паре с точным именем `csrf_token`.
  Duplicate token и любое дополнительное поле отклоняются
  (`src/skillhub/web/_reload_guard.py:137-151`).
- До constant-time compare обе строки кодируются как ASCII. Unicode, включая
  прежнее `%D1%8F`, даёт `False`, а не исключение. Затем submitted и expected
  обязаны совпасть с точным URL-safe форматом из 43 символов; только после
  этого вызывается байтовый `secrets.compare_digest`
  (`src/skillhub/web/_reload_guard.py:154-164`).
- Ошибка остаётся типизированным 403 `reload_forbidden`; token/body не
  включаются в response или прикладной log
  (`src/skillhub/web/errors.py:26-32`,
  `src/skillhub/web/errors.py:86-96`).
- Committed matrix покрывает missing/wrong/duplicate/extra/invalid UTF-8 и
  percent-encoded Unicode token, проверяет отсутствие reload, сохранение той
  же snapshot-ссылки и отсутствие token/private body в response/log
  (`tests/test_e1_s4_qa.py:333-487`).

`SEC-E1S4-001` закрыт.

## PRG marker: storage, replay, reflection, cache и referrer

- Успешный POST не рендерит HTML и не возвращает token: он выдаёт 303 только
  на относительный `/skills?reload=<server-number>`
  (`src/skillhub/web/routes.py:78-87`).
- Marker создаётся сервером как монотонное decimal значение. Входной marker
  ограничен 20 символами и только decimal grammar; Unicode decimal может
  пройти grammar, но не совпадает с ASCII server marker и безопасно
  сваливается к текущему поколению без признака reload
  (`src/skillhub/web/_catalog.py:83-117`).
- Хранилище находится только в памяти app instance и ограничено восемью
  поколениями. Угаданный или повторно открытый свежий marker может повторно
  прочитать только соответствующее публичное поколение; мутацию он не
  запускает. Вытесненный, неизвестный, слишком длинный или malformed marker
  возвращает текущее поколение без ложного `reload_performed`
  (`src/skillhub/web/_catalog.py:11-13`,
  `src/skillhub/web/_catalog.py:100-117`).
- Marker не является capability: он последовательный и replayable, но за ним
  доступны только те же `name/caption/description/has_files` и безопасные
  issue-сообщения, которые уже публичны на `/skills` и `/api/skills`.
  CSRF-token и тело скилла в URL не попадают
  (`src/skillhub/web/_catalog.py:15-57`,
  `src/skillhub/web/routes.py:83-86`).
- User-controlled raw marker не отражается в template context, HTML,
  JavaScript, logger или redirect. Он используется только для точного поиска
  в bounded tuple. Redirect marker создаётся приложением
  (`src/skillhub/web/routes.py:63-75`;
  `src/skillhub/web/_catalog.py:109-117`).
- После показа точного результата локальный JavaScript ставит фокус и удаляет
  `reload` через same-path `history.replaceState`. Скрипт не строит HTML и не
  делает network request; неизвестные query-параметры остаются
  URL-encoded browser API
  (`src/skillhub/web/static/app.js:1-13`).
- Явного `Cache-Control: no-store` у token-bearing GET нет. В поддерживаемом
  контуре это не даёт подтверждённой утечки: HTTP cache изолирован origin,
  token не находится в URL, после рестарта создаётся новый token, а
  cross-origin Referer полностью подавляется.
- `Referrer-Policy: same-origin` слабее прежнего S1 `no-referrer`, но в
  текущем UI обоснованно не раскрывает данные чужому origin: все CSS, JS,
  favicon, формы и навигация same-origin; CSP разрешает ресурсы только `self`;
  внешних ссылок и subresource нет; token остаётся только в POST body.
  Полный path/query может идти только уже доверенному same-origin приложению,
  которое и выдало страницу
  (`src/skillhub/web/security.py:24-30`;
  `src/skillhub/web/templates/base.html:1-20`;
  `src/skillhub/web/templates/skills.html:69-94`,
  `src/skillhub/web/templates/skills.html:155-158`).

Server-side marker не погашается после первого GET, поэтому буквальная
одноразовость обеспечивается только browser history cleanup, а не
consume-on-read. Это подтверждённая P3-находка ниже.

## Finding

### P3 — Последовательный PRG-marker повторно показывает устаревшее поколение

Идентификатор: `SEC-E1S4-R1-001`.

**Достижимость.** Первый успешный POST получает marker `"1"`, следующие —
монотонные `"2"`, `"3"` и далее. `_flashes` сохраняет последние восемь
поколений, а `for_marker()` только ищет совпадение и никогда не удаляет его.
Повторные GET одного `/skills?reload=N` каждый раз возвращают тот же report,
ставят `reload_performed=True` и показывают «Каталог перезагружен» либо
старое предупреждение
(`src/skillhub/web/_catalog.py:83-117`;
`src/skillhub/web/templates/skills.html:20-61`).

GET не требует Origin или CSRF, что правильно для безопасной навигации, но
делает marker достижимым из cross-site top-level navigation и для любого
локального HTTP-клиента. Значение предсказуемо, не связано с инициатором POST
и не является одноразовым. JavaScript удаляет параметр только из history
конкретной успешно отрисованной вкладки; он не погашает server state,
не защищает прямой повтор URL и не работает при отключённом/неисполненном
скрипте (`src/skillhub/web/static/app.js:3-13`).

**Влияние.** Атакующий может заставить локальный UI показать старые карточки,
старую безопасную причину пропуска и ложное утверждение о только что
выполненном reload. Удалённые из текущего каталога публичные metadata остаются
доступны по угадываемому URL до восьми следующих reload. Это нарушение
целостности статуса и заявленной одноразовой PRG-семантики
(`docs/handoff/e1-s4-ux-client.md:120-131`).

Мутация не повторяется, CSRF-token не находится в URL, `Skill.body` и
absolute path не проецируются, history bounded восемью поколениями, а SOP не
даёт чужому origin прочитать ответ. Поэтому приоритет P3, а не P2.

**Требуемое закрытие.** Flash-id должен быть непредсказуемым, bounded,
погашаться атомарно при первом GET и хранить только необходимую публичную
проекцию, а не полный `LoadReport`. Token-bearing/flash response следует
проверить с `Cache-Control: no-store`, чтобы browser cache не восстанавливал
уже погашенное состояние. Regression-oracle: первый GET точного redirect
показывает результат, второй GET того же URL и refresh/bookmark показывают
обычное текущее состояние; неизвестный/просроченный marker ничего не отражает
и не меняет.

## Issue messages, XSS и data exposure

- S2 создаёт `LoadIssue` только со stable reason и безопасным относительным
  identifier; hostile имя заменяется на bounded SHA-256 identifier, root
  представлен `"."`
  (`src/skillhub/registry/_registry.py:137-155`,
  `src/skillhub/registry/_registry.py:189-196`,
  `src/skillhub/registry/_registry.py:217-225`).
- `_issue_messages.py` выбирает текст и действие из фиксированных карт.
  Root identifier не показывается. Неизвестная причина получает фиксированный
  fallback; raw reason, exception, body и absolute root в message не
  форматируются
  (`src/skillhub/web/_issue_messages.py:5-78`,
  `src/skillhub/web/_issue_messages.py:81-104`).
- Для skill-level текста форматируется только уже безопасный `issue.path`.
  `reason` остаётся машинным полем внутреннего context, но шаблон выводит
  только `message` и `action`
  (`src/skillhub/web/_catalog.py:184-192`;
  `src/skillhub/web/templates/skills.html:53-60`).
- Jinja environment использует штатный autoescape для `.html`; metadata,
  query и issue вставляются обычными выражениями без `safe`/`Markup`.
  Атрибут `data-skill-name` и hidden token также экранируются
  (`src/skillhub/app_factory.py:57-64`;
  `src/skillhub/web/templates/skills.html:13-18`,
  `src/skillhub/web/templates/skills.html:53-60`,
  `src/skillhub/web/templates/skills.html:79-94`,
  `src/skillhub/web/templates/skills.html:113-129`).
- Единственный client script использует только фиксированные строки в
  `textContent`; `innerHTML`, `document.write`, eval и dynamic script URL
  отсутствуют. CSP, `nosniff`, XFO DENY и same-origin static root сохранены
  (`src/skillhub/web/static/app.js:15-28`;
  `src/skillhub/web/security.py:11-30`;
  `src/skillhub/app_factory.py:53-59`).
- Committed oracles проверяют hostile caption/description/query, отсутствие
  dangerous nodes/sinks, безопасный известный issue и fallback неизвестной
  причины без raw reason/body/absolute path
  (`tests/test_e1_s4_qa.py:272-286`,
  `tests/test_e1_s4_qa.py:671-689`;
  `tests/test_skills_catalog.py:637-694`,
  `tests/test_skills_catalog.py:864-901`).

Подтверждённого reflected/stored XSS или раскрытия тела/абсолютного пути нет.

## Atomic generation, async lock и cancellation

- `LoadReport` теперь сам ищет по своему immutable tuple через общую
  `_search_skills`; web больше не смешивает отдельно прочитанный registry
  snapshot с summary
  (`src/skillhub/registry/_models.py:40-85`;
  `src/skillhub/registry/_registry.py:80-95`).
- `_CatalogGeneration` одним frozen объектом связывает report, производный
  `ReloadSummary` и marker. `GET /api/skills`, обычная страница и marker-page
  сначала захватывают одну ссылку поколения, а затем ищут и рендерят только
  её (`src/skillhub/web/_catalog.py:61-72`,
  `src/skillhub/web/routes.py:54-75`).
- Reload полностью строит новое поколение до одного присваивания `_current`.
  Старые поколения неизменяемы и остаются пригодны для точного PRG-ответа
  (`src/skillhub/web/_catalog.py:95-107`).
- Per-app AnyIO `Lock` захватывается до `run_in_threadpool()`. Только владелец
  gate занимает worker token и входит в registry reload; остальные запросы
  асинхронно ждут, не занимая общий pool
  (`src/skillhub/web/routes.py:31-44`,
  `src/skillhub/web/routes.py:78-83`).
- Внутренний S3 `threading.Lock` остаётся defense-in-depth для иных
  process-local callers и по-прежнему охватывает полную S2-загрузку,
  построение snapshot и одно присваивание
  (`src/skillhub/registry/_registry.py:32-50`,
  `src/skillhub/registry/_registry.py:69-78`).
- Реализация AnyIO gate допускает отмену waiter до worker dispatch.
  Committed oracle удерживает первый reload, отменяет второй и подтверждает,
  что третий является лишь вторым фактическим loader call, но момент входа
  второго именно в lock queue не синхронизирован отдельным сигналом
  (`tests/test_skills_catalog.py:548-634`).
- Конкурентный oracle различает два report и подтверждает точные
  cards/issues каждого redirect marker и целостность промежуточных API/UI
  чтений (`tests/test_skills_catalog.py:413-475`). Отдельный oracle с
  ограниченным worker pool подтверждает доступность sync GET во время
  ожидающего reload (`tests/test_skills_catalog.py:478-545`).

Прежние atomicity и worker-pool blockers закрыты. Автоматического retry нет;
отклонённый request не входит в gate и не меняет поколение. Слабость
server-side PRG replay отделена в `SEC-E1S4-R1-001`.

## Сохранение гарантий S1–S3

### S1: process, HTTP и logs

- Поддерживаемый launcher остаётся на `127.0.0.1` без access log; settings и
  startup failure boundary в delta не менялись
  (`src/skillhub/_server.py:84-108`).
- ProcessErrorBoundary, безопасные expected/framework envelopes,
  request-context cleanup и bounded allowlist logging сохранены. Новый Host
  guard логирует только фиксированные поля
  (`src/skillhub/web/_boundaries.py:21-55`,
  `src/skillhub/web/_boundaries.py:107-148`;
  `src/skillhub/web/errors.py:50-124`;
  `src/skillhub/web/_host_guard.py:29-38`).
- CSP, nosniff, XFO и request ID сохранены на success и отказах. Изменённый
  ранее `same-origin` referrer рассмотрен отдельно выше
  (`src/skillhub/web/security.py:11-59`).

### S2: filesystem и parsing

- Delta не меняет `RegistryFilesystem`, platform handle/reparse policy,
  containment, strict UTF-8, safe YAML и файловые бюджеты. Web по-прежнему
  передаёт только доверенно настроенный root и не принимает path/root из HTTP
  (`src/skillhub/app_factory.py:27-40`;
  `src/skillhub/registry/_registry.py:98-123`).
- Предел одной загрузки остаётся 128 кандидатов и 1 MiB тел, а каждый
  `SKILL.md` ограничен 64 KiB; issue остаётся safe identifier/stable reason
  (`src/skillhub/registry/_limits.py:1-19`;
  `src/skillhub/registry/_registry.py:189-225`).
- Добавленный `LoadReport.search()` не читает файлы и не меняет report; это
  чистый поиск по уже проверенному immutable tuple
  (`src/skillhub/registry/_models.py:40-85`).

### S3: snapshot и поиск

- Production constructor, единый private S2 loader, non-reentrant reload
  lock, copy-on-write `MappingProxyType`, lock-free snapshot readers и
  first-wins duplicate policy сохранены
  (`src/skillhub/registry/_registry.py:32-78`,
  `src/skillhub/registry/_registry.py:130-133`,
  `src/skillhub/registry/_registry.py:184-187`).
- Registry и `LoadReport` используют одну `_search_skills()` с Unicode
  `casefold`; web generation не создаёт альтернативную семантику поиска
  (`src/skillhub/registry/_models.py:69-85`;
  `src/skillhub/registry/_registry.py:80-95`).

## A–I

### A. Input validation

Строго проверяются единственный ASCII Host, local authority и порт,
canonical Origin, Content-Type, declared/actual body, strict form grammar,
единственное имя поля и точный ASCII token format. Query ограничен 128
Python-символами до поиска. Fail-open веток в delta не найдено
(`src/skillhub/web/_host_guard.py:20-70`;
`src/skillhub/web/_reload_guard.py:23-164`;
`src/skillhub/web/_catalog.py:121-153`).

### B. Authz / tenant

N/A для users, tenant и RBAC: приложение локальное и unauthenticated.
CSRF-token — browser capability против cross-site mutation, а не
аутентификация локального процесса. Configured root не выбирается из HTTP.

### C. Secrets / logs

CSRF-token имеет 256 бит исходной CSPRNG-энтропии, живёт только в памяти
одного app instance, не попадает в URL/log/storage. Query, Host, form body,
skill body, raw reason и absolute root не передаются прикладному logger.
PRG-marker не секретен. Подтверждённой cache/referrer утечки секрета нет;
stale metadata retention входит в `SEC-E1S4-R1-001`.

### D. Injection / files

Host/Origin/form не интерпретируются как путь или код. HTTP не принимает
skills root/path/body. S2 containment, no-follow, byte/tree budgets, safe
YAML, strict UTF-8 и duplicate first-wins не изменены. Issue path — только
safe identifier; Jinja и JavaScript не создают executable sink.

### E. Crypto

`token_urlsafe(32)` использует системный CSPRNG; token имеет точный URL-safe
ASCII формат, а равенство проверяется `secrets.compare_digest()` над bytes.
Собственных подписей, шифрования, KDF или key storage в слайсе нет.

### F. HTTP / network

Bind — `127.0.0.1`; Host allowlist точный; CORS отсутствует. Origin сравнивает
scheme/casefold-host/normalized-port с тем же строгим Host. Security headers и
safe error envelopes охватывают Host rejection. Proxy trust остаётся
ответственностью ASGI runner вне поддерживаемого direct-local запуска.

### G. Replay / integrity

Повтор валидного reload идемпотентен относительно карточек, но намеренно
выполняет новую bounded загрузку. CSRF-token переиспользуется в пределах app
lifetime. Публикация registry snapshot и web generation атомарна на своих
указателях. Предсказуемый read-only replay PRG-поколения и ложного статуса —
`SEC-E1S4-R1-001`.

### H. DoS / bounds

Host decimal parse short-circuit bounded, query — 128 символов, reload body —
4096 байт, form fields — максимум два, одна файловая загрузка наследует S2
budgets. Async gate допускает только один worker reload; отменённый waiter не
запускается. Число ожидающих HTTP-задач и последовательных reload scans не
coalesce, но cross-site attacker не получает token/Origin path, а локальный
процесс не является изолированным principal в принятой модели.

### I. Stored XSS / data exposure

Metadata/query/issues проходят Jinja autoescape, client code использует
`textContent`, CSP запрещает object/base/framing и ограничивает ресурсы self.
HTTP-проекция не содержит `Skill.body` или filesystem path. Bounded PRG
history удерживает полные старые `LoadReport` в process memory и повторно
проецирует публичные metadata/issue по угадываемому marker —
`SEC-E1S4-R1-001`.

### J. LLM

N/A: LLM-scope **НЕТ**. В диапазоне нет model call, prompt dispatch,
tool/agent execution, обработки model output или динамического исполнения
`SKILL.md`; файлы только валидируются и каталогизируются как данные
(`docs/phases/orchestrator-e1.md:219-260`).

## Attack-test gaps

Ниже неблокирующие пробелы доказательной базы; подтверждённой уязвимости за
ними не найдено.

1. Host matrix не прогонялась raw-socket проверкой реального Uvicorn/h11 и не
   покрывает HTTP/2 adapter, mixed-case local host, очень длинную строку из
   одних нулей и active WebSocket scope. HTTP mutation/WebSocket routes в
   текущем слайсе отсутствуют, а parser fail-closed.
2. Origin oracles не перечисляют userinfo/password, path/query/fragment,
   malformed IPv6, Unicode/NFKC authority, explicit default-port equivalence,
   mixed-case hostname и реальный proxy-header path. Общий parser запрещает
   эти неоднозначные формы либо сравнивает canonical tuple.
3. CSRF matrix не фиксирует отдельно token длиной 42/44, NUL, malformed
   percent escape, дубликаты Content-Type/Content-Length, declared exact
   4096, chunked 4097 и большое число пустых chunks. Actual-body и exact token
   bounds остаются независимыми от declared length.
4. Committed cancellation oracle не имеет детерминированного сигнала, что
   второй запрос уже ждёт именно AnyIO lock. Также не покрыты active thread
   cancellation, disconnect после начала filesystem scan и shutdown при
   зависшем OS I/O. Явного timeout/coalescing нет; одна загрузка bounded по
   объёму, но не по latency файловой системы.
5. Нет burst-oracle на очень большое число валидных async waiters. Gate
   ограничивает active worker одним, но сохраняет `O(r)` pending request state
   и последовательно выполняет каждый неотменённый reload.
6. Помимо `SEC-E1S4-R1-001`, не проверены вытеснение после восьми поколений,
   malformed/Unicode/21-character marker, гонка двух первых GET одного
   будущего consume-on-read flash и отсутствие полного `LoadReport` в
   retention.
7. Нет browser/network oracle для `Cache-Control`, back/forward cache,
   cross-origin отсутствия Referer и same-origin Referer до static assets.
   Header `same-origin` утверждается тестом, но прежний S1 `no-referrer`
   defense-in-depth не восстановлен
   (`tests/test_web.py:204-213`).
8. Нет browser-level execution oracle именно для нового issue message/action
   и PRG history cleanup. HTMLParser и статический sink audit не наблюдают
   реальное выполнение DOM/CSP.
9. Восемь PRG-поколений удерживают полные immutable `LoadReport`, включая
   непубличные bodies, то есть до примерно восьми S2 byte budgets плюс object
   overhead. HTTP-ссылки на body отсутствуют, но отдельного memory-retention
   oracle нет.
10. Полный S1–S3 regression suite в этом re-gate не запускался по условию;
    вывод основан на неизменённых границах, точной delta и committed
    attack-oracles.

## Проверка диапазона

- `git merge-base 11393ba 7fe15c7` →
  `11393baf8a9f4cc6e421c81868c007b7c4d512c6`.
- Диапазон содержит один commit:
  `7fe15c7 fix(web): устранить замечания E1-S4`.
- `git diff --check 11393ba..7fe15c7` → успешно.
- До записи handoff worktree был чист.
- Код и тесты не изменялись и не запускались; записан только
  `docs/handoff/e1-s4-rework-1-security.md`; commit не создавался.
- Во время финальной проверки появились параллельные незакоммиченные изменения
  двух test-файлов и чужие rework handoff. Они не создавались этой ролью и не
  использовались как доказательство целевого commit.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 1. AppSec gate не пройден.
