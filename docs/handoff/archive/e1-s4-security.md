# Выжимка

Роль: security
Scope: `e4feefa..f2c6b9f`
AppSec-scope: **ДА**
LLM-scope: **НЕТ**
Вердикт: **НУЖНА_ДОРАБОТКА**
Findings: 1 — P0: 0, P1: 0, P2: 0, P3: 1.

Каталог стартует из доверенного configured root, проходит неизменённую границу
S2 и публикуется неизменённым S3-механизмом. API возвращает только четыре
публичных metadata-поля; query ограничен до поиска и не попадает в прикладные
логи. Jinja autoescape и единственный статический JavaScript используют
текстовые sink. Reload проверяет точный Origin, тип и размер тела, затем токен,
и только после этого вызывает реестр; отклонённые запросы снимок не меняют.

Gate блокирует один P3: percent-encoded не-ASCII `csrf_token` успешно
разбирается в Python `str`, после чего `secrets.compare_digest()` выбрасывает
`TypeError`. Запрос даёт безопасный 500 вместо контролируемого 403. Reload не
происходит, токен и внутренние данные не раскрываются, процесс не падает,
поэтому это P3, а не P2.

# Отчёт

## Договор, модель угроз и delta

- E1-S4 требует ровно пять режимов, единый case-insensitive поиск API/UI,
  защищённый reload, безопасный результат для битого шестого каталога и
  текстовое отображение hostile metadata при сохранении CSP, TrustedHost,
  CORS off и localhost bind
  (`docs/phases/orchestrator-e1.md:219-262`;
  `docs/phases/epic-e1-foundation-registry.md:63-77`).
- Граница браузер → FastAPI считает недоверенными все поля формы. Каталог
  также недоверенный, но configured root является доверенной конфигурацией.
  Issue/log обязаны содержать только стабильную причину и безопасный
  идентификатор (`docs/security/threat-model.md:15-21`,
  `docs/security/threat-model.md:31-61`).
- Для cross-site mutation модель угроз требует same-origin, CSRF, CORS off и
  TrustedHost; HTML/JavaScript должны получать только контекстно
  закодированные значения (`docs/security/threat-model.md:64-79`).
- Диапазон содержит один commit
  `f2c6b9f feat: добавить каталог пяти скиллов`. Он добавляет пять каталогов
  скиллов, JSON/UI/reload routes, DTO, форму, локальный JavaScript и проверки
  тела/Origin/CSRF. `git merge-base e4feefa f2c6b9f` равен `e4feefa`;
  `git diff --check e4feefa..f2c6b9f` успешен.
- Production-код и тесты `skillhub.registry` в диапазоне не менялись.
  `_server.py`, accepted error boundary, настройки и лог-redaction также не
  менялись. Из S1 security baseline изменён только `Referrer-Policy`:
  `no-referrer` → `same-origin`
  (`src/skillhub/web/security.py:172-187`).

## Фактический поток данных и изменения состояния

1. `create_app()` получает только программно переданный доверенный
   `skills_root` либо относительный штатный `Path("skills")`, создаёт один
   `SkillRegistry` и синхронно выполняет начальный `reload()` до публикации
   маршрутов. Поддерживаемая команда должна запускаться из корня репозитория и
   слушает только `127.0.0.1`
   (`src/skillhub/app_factory.py:21-39`;
   `src/skillhub/_server.py:100-108`;
   `README.md:13-24`).
2. После начальной загрузки создаётся отдельный
   `secrets.token_urlsafe(32)` на экземпляр приложения. Токен передаётся
   объекту routes, хранится только в памяти и выводится в hidden field
   `/skills`; cookie, URL, localStorage и sessionStorage не используются
   (`src/skillhub/app_factory.py:37-60`;
   `src/skillhub/web/routes.py:129-152`;
   `src/skillhub/web/templates/skills.html:81-88`).
3. `GET /api/skills` и `GET /skills` принимают единственный строковый `q`.
   До `registry.search()` обе ветки проверяют предел 128 Python-символов.
   API строит новый `SkillSummary`, содержащий только `name`, `caption`,
   `description`, `has_files`; `body` и файловый путь в DTO отсутствуют
   (`src/skillhub/web/routes.py:20-46`,
   `src/skillhub/web/routes.py:172-218`,
   `src/skillhub/web/routes.py:310-324`).
4. `POST /skills/reload` сначала вызывает `validate_reload_request()`. Только
   после его успешного завершения `_CatalogState.reload()` передаётся в
   threadpool. Следовательно, Origin/Host, media type, declared/actual body,
   form grammar и token проверяются до любого файлового reload
   (`src/skillhub/web/routes.py:235-257`;
   `src/skillhub/web/security.py:25-46`).
5. `_CatalogState` отдельным `Lock` сериализует HTTP-reload и атомарно
   связывает возвращённый safe summary с завершённым registry report. Сам
   `SkillRegistry.reload()` сохраняет принятую S3-блокировку и одно
   присваивание snapshot; readers продолжают брать целую старую или новую
   ссылку (`src/skillhub/web/routes.py:91-127`;
   `src/skillhub/registry/_registry.py:68-98`).
6. `LoadIssue` переводится только в `path`/`reason`; оба значения уже
   нормализованы S2. Тело `SKILL.md`, абсолютный root и exception text в
   context не передаются. Missing root становится `(".", "root_missing")`,
   API остаётся пустым, а UI показывает безопасный результат
   (`src/skillhub/web/routes.py:327-336`;
   `src/skillhub/registry/_registry.py:100-116`,
   `src/skillhub/registry/_registry.py:193-229`).

## Finding

### P3 — Не-ASCII CSRF token вызывает необработанный `TypeError` и 500

Идентификатор: `SEC-E1S4-001`.

**Достижимость.** После успешных exact-Origin и Content-Type проверок тело
`csrf_token=%D1%8F` проходит strict UTF-8 и `parse_qsl()`. Результат — одна
допустимая пара с именем `csrf_token` и значением `"я"`, поэтому
`_parse_form_token()` возвращает строку вызывающему коду
(`src/skillhub/web/security.py:118-124`,
`src/skillhub/web/security.py:155-169`).

**Причина.** Expected token, сгенерированный `token_urlsafe`, является ASCII,
но `secrets.compare_digest()` для строк разрешает только ASCII. Сравнение
attacker-controlled Unicode `str` с expected ASCII `str` выбрасывает
`TypeError`; `validate_reload_request()` это исключение не нормализует в
`ReloadForbiddenError`
(`src/skillhub/web/security.py:40-46`;
`src/skillhub/app_factory.py:37-39`).

**Наблюдаемый исход.** Диагностическая runtime-проба целевого commit отправила
same-origin POST с `application/x-www-form-urlencoded` и
`csrf_token=%D1%8F`. Ответ: 500 с безопасным `internal_error`; снимок до и
после совпал. Process boundary записал только `error_type="TypeError"`,
фиксированный code/status и request ID, без значения токена
(`src/skillhub/web/_boundaries.py:21-55`,
`src/skillhub/web/_boundaries.py:107-148`).

**Влияние и приоритет.** Недоверенный запрос переводит штатный отказ
capability-проверки в unexpected-error path и 500. Это нарушает
предсказуемость fail-closed HTTP-границы и позволяет локальному клиенту
создавать ошибочные server events. При этом файловый reload недостижим,
снимок не меняется, секрет не раскрывается, response санитизирован, процесс
остаётся доступен, а поддерживаемый сервер привязан к loopback. Поэтому
приоритет P3.

**Требуемое закрытие.** До constant-time compare отклонять любое значение вне
точного ASCII/token-url-safe формата как `ReloadForbiddenError` либо
сравнивать байтовые значения после безопасного кодирования. Regression-oracle
должен отправить percent-encoded UTF-8 token и подтвердить 403,
`reload_forbidden`, отсутствие reload и отсутствие token/body в response/log.

## Origin, Host, scheme, port и proxy trust

- Header принимается только если существует ровно одно значение `Origin` и
  ровно одно значение `Host`; отсутствие или дубликат даёт `None` и
  fail-closed отказ (`src/skillhub/web/security.py:49-59`).
- Origin обязан быть непустым, не равным `null` и не содержать whitespace.
  Разрешены только `http`/`https`, hostname обязателен, userinfo/password,
  path, query и fragment запрещены. Raw value должен точно совпасть с
  `scheme://netloc`, поэтому suffix после origin, slash, управляющая вставка
  и неоднозначный остаток не принимаются
  (`src/skillhub/web/security.py:62-94`,
  `src/skillhub/web/security.py:105-115`).
- Сравнивается tuple `(scheme, hostname.casefold(), port)`. Неявные порты
  нормализуются к 80/443; неверный, выходящий за диапазон или отличающийся
  explicit port отклоняется. Scheme mismatch также отклоняется
  (`src/skillhub/web/security.py:88-102`).
- Expected origin строится из ASGI scheme и единственного raw Host, после чего
  проходит тот же строгий parser. `http://localhost` не равен
  `http://127.0.0.1`; `localhost.attacker.example` не равен `localhost`.
  Userinfo в Origin или в crafted Host не проходит parser
  (`src/skillhub/web/security.py:49-54`,
  `src/skillhub/web/security.py:105-115`).
- Внешний TrustedHost разрешает только точные `127.0.0.1`, `localhost` и
  test-only `testserver`; wildcard отсутствует. Поддерживаемый launcher
  слушает только IPv4 loopback. `[::1]` не входит в allowlist и фактически не
  обслуживается этим bind, то есть IPv6 закрыт, а не неявно доверен
  (`src/skillhub/app_factory.py:40-47`;
  `src/skillhub/_server.py:100-108`;
  `src/skillhub/web/_boundaries.py:58-104`).
- Приложение не читает `Forwarded` или `X-Forwarded-Host`. ASGI scheme,
  однако, принадлежит серверу: Uvicorn по умолчанию включает proxy-headers и
  доверяет их только от configured forwarded peers; в поддерживаемом
  loopback-запуске peer по умолчанию `127.0.0.1`. Cross-site HTML form не
  может задать proxy headers, custom-header fetch требует preflight, а CORS
  middleware отсутствует. При внешнем ASGI deployment список доверенных
  proxy и scheme остаются ответственностью runner, как явно указано в README
  (`README.md:25-30`). Подтверждённого Origin bypass в принятом deployment
  нет.

## Reload body, token и leakage

- Origin проверяется до чтения тела. Затем принимается только одно значение
  `Content-Type` с media type
  `application/x-www-form-urlencoded`; multipart, JSON, отсутствующий и
  дублированный Content-Type отклоняются
  (`src/skillhub/web/security.py:25-42`,
  `src/skillhub/web/security.py:118-124`).
- Один `Content-Length` разбирается до stream; отрицательное, нечисловое и
  значение больше 4096 даёт 413. Независимо от declared length фактический
  stream суммируется по chunks и прерывается при 4097-м байте. `parse_qsl()`
  вызывается только после bounded join
  (`src/skillhub/web/security.py:127-152`).
- Form parser использует strict UTF-8, strict parsing и максимум два поля, а
  результат принимает только одну пару с точным именем `csrf_token`.
  Дубликат или дополнительное поле не проходит
  (`src/skillhub/web/security.py:155-169`).
- `token_urlsafe(32)` даёт 32 случайных байта, то есть 256 бит исходной
  энтропии, и новый token для каждого app instance. Он хранится в памяти
  routes, не записывается в cookie, URL, файл или browser storage. Успешный
  ASCII compare выполняется через `secrets.compare_digest`
  (`src/skillhub/app_factory.py:37-60`;
  `src/skillhub/web/routes.py:129-152`;
  `src/skillhub/web/static/app.js:1-18`).
- Страница с hidden token не задаёт `Cache-Control: no-store`; глобальный
  referrer policy в delta ослаблен до `same-origin`. Подтверждённой утечки
  capability из этого не следует: token никогда не находится в URL,
  cross-origin Referer запрещён политикой, внешних subresource нет, CSP
  разрешает только self, а HTTP cache остаётся под same-origin browser
  boundary. После рестарта cached token становится недействительным, потому
  что app создаёт новый token. Тем не менее no-store и восстановление
  `no-referrer` остаются разумным defense-in-depth и перечислены ниже как
  пробел проверки, не как отдельная P0–P3 уязвимость.

## HTML, JavaScript, CSP, CORS и ошибки

- Starlette создаёт Jinja environment с
  `autoescape=select_autoescape()` для `.html`. Metadata, query и issue
  вставляются обычными `{{ ... }}` без `safe`/`Markup`; hostile caption,
  description, query и safe issue path остаются текстом
  (`src/skillhub/app_factory.py:48-53`;
  `src/skillhub/web/templates/skills.html:13-17`,
  `src/skillhub/web/templates/skills.html:46-52`,
  `src/skillhub/web/templates/skills.html:67-74`,
  `src/skillhub/web/templates/skills.html:106-122`).
- Единственный новый JavaScript выбирает статические элементы и присваивает
  только фиксированные строки в `textContent`. `innerHTML`, `outerHTML`,
  `insertAdjacentHTML`, `eval`, dynamic script URL, storage и network sink
  отсутствуют (`src/skillhub/web/static/app.js:1-18`).
- CSP остаётся `default-src 'self'; object-src 'none'; base-uri 'none';
  frame-ancestors 'none'`; XFO DENY и nosniff сохранены. Same-origin script
  берётся только из package-owned static root; skill root не mounted
  (`src/skillhub/web/security.py:17-20`,
  `src/skillhub/web/security.py:172-187`;
  `src/skillhub/app_factory.py:48-53`;
  `src/skillhub/web/templates/skills.html:144-147`).
- CORS middleware и `Access-Control-Allow-*` отсутствуют. Cross-origin
  readable API/reload response не появляется; простой cross-site form
  дополнительно блокируется exact Origin и неизвестным token. TrustedHost
  остаётся перед route execution.
- Expected 403/413/415 и query 422 возвращают фиксированные code/message и
  логируют только error code/status. Unexpected path finding-а также
  санитизирован внешней process boundary; body, path, Host, query и token не
  отражаются (`src/skillhub/web/errors.py:18-49`,
  `src/skillhub/web/errors.py:52-124`;
  `src/skillhub/web/_boundaries.py:21-55`).

## A–I

### A. Input validation

Query ограничен 128 символами до поиска в API и UI. Reload принимает только
один Origin/Host/Content-Type, ограниченный form body и одну token-пару.
Не-ASCII token не нормализован в ожидаемый 403 — `SEC-E1S4-001`.

### B. Authz / tenant

N/A для users/tenants/RBAC: это локальный unauthenticated E1-инструмент.
CSRF-token является capability против browser cross-site request, но не
аутентификацией локального процесса. Trusted configured root не выбирается из
HTTP.

### C. Secrets / logs

DeepSeek key в E1 не нужен. CSRF-token имеет 256 бит исходной энтропии,
хранится в памяти экземпляра и не попадает в URL/log/storage. Query, skill
body, абсолютный root и broken body не передаются прикладному logger.
`same-origin` referrer и отсутствие `no-store` не дали подтверждённой
cross-origin утечки, но требуют отдельного regression-oracle.

### D. Injection / files

HTTP не принимает root/path/body скилла. Configured root проходит неизменённые
S2 containment, no-follow, byte/tree budgets, strict UTF-8, safe YAML и
duplicate first-wins. Файлы остаются данными и не импортируются. Issue
содержит только safe identifier и stable reason.

### E. Crypto

`token_urlsafe(32)` использует CSPRNG и достаточную энтропию; успешный ASCII
путь использует constant-time compare. Типовая граница compare не закрыта для
Unicode attacker input — `SEC-E1S4-001`. Подписей, шифрования и key derivation
в delta нет.

### F. HTTP / network

Поддерживаемый bind — `127.0.0.1`; TrustedHost wildcard отсутствует; CORS off.
Origin сравнивает scheme/casefold-host/normalized-port и запрещает
userinfo/null/suffix/path/query/fragment. IPv4 alias не считается тем же
origin, IPv6 не разрешён deployment-ом. Proxy scheme доверяется ASGI runner;
приложение не доверяет forwarded host.

### G. Replay / integrity

Token намеренно переиспользуется в пределах app lifetime, что нормально для
CSRF-token без пользовательской сессии. Каждый принятый reload сериализован и
публикует целый S3 snapshot; автоматического retry нет. Любой отказ до
threadpool, включая `SEC-E1S4-001`, оставляет snapshot неизменным. Повторный
валидный reload не дублирует карточки.

### H. DoS / bounds

Query ограничен до casefold/search; reload body ограничен declared и actual
4096 bytes до form parse; число form fields ограничено. Файловый reload
наследует S2 root-entry/candidate/byte/depth/link budgets и сериализуется.
Unicode token создаёт дешёвый 500/event, но не удерживает lock и не запускает
I/O; это availability-аспект P3 finding-а.

### I. Stored XSS / data exposure

Caption/description/name, query и issue проходят HTML autoescape; body скилла
не входит ни в API, ни в template context. Static JS использует только
`textContent`, API отдаёт `application/json` под `nosniff`, skill directory не
публикуется статически. CSP/XFO блокируют inline/external execution и framing
в текущей поверхности.

### J. LLM

N/A: LLM-scope **НЕТ**. В диапазоне нет model call, prompt dispatch,
tool/agent execution или обработки model output. Тела `SKILL.md` только
валидируются и хранятся реестром; HTTP публикует лишь metadata
(`docs/phases/orchestrator-e1.md:219-262`;
`src/skillhub/web/routes.py:30-46`,
`src/skillhub/web/routes.py:315-324`).

## Attack-test gaps

1. Нет oracle для percent-encoded non-ASCII token; существующая wrong-token
   ветка использует только ASCII и не обнаруживает `SEC-E1S4-001`
   (`tests/test_skills_catalog.py:382-427`).
2. Origin matrix не покрывает userinfo/password, path/query/fragment,
   duplicate Origin/Host, malformed/explicit default port, scheme mismatch,
   case normalization, crafted Host после Starlette split, IPv4 alias, IPv6,
   `Forwarded`, `X-Forwarded-Host` и реальный Uvicorn proxy-header path
   (`tests/test_skills_catalog.py:382-427`).
3. Body oracle проверяет только фактические 4097 bytes. Нет отдельных
   проверок oversized/negative/non-numeric/duplicate `Content-Length`,
   chunked body без length, malformed UTF-8, duplicate/extra token field,
   invalid percent encoding и 415; для каждого нужен явный no-reload oracle
   (`tests/test_skills_catalog.py:458-477`).
4. Нет проверки 403/413/415 reload responses на отсутствие CORS и наличие CSP,
   request ID и safe logs. Текущий CORS-тест покрывает GET API и hostile Host,
   а старые tests — 404/405
   (`tests/test_skills_catalog.py:552-567`;
   `tests/test_web.py:354-383`).
5. Нет browser-level execution oracle hostile metadata. HTMLParser проверяет
   отсутствие созданных dangerous nodes и статически ищет `innerHTML`, но не
   наблюдает DOM/script execution в браузере
   (`tests/test_skills_catalog.py:479-516`).
6. Нет oracle для `Cache-Control` token-bearing pages, переходов
   back/forward, cross-origin Referer и сохранения прежнего S1
   `no-referrer`. Текущий тест утверждает только глобальный
   `same-origin` header (`tests/test_web.py:204-213`).
7. Модель угроз всё ещё формулирует изменяющий путь как JSON-only, тогда как
   S4 реализует строго ограниченную urlencoded server form. Текущая защита не
   полагается на JSON/preflight и имеет независимые Origin+token controls, но
   канон следует синхронизировать после решения владельца
   (`docs/security/threat-model.md:75-79`;
   `src/skillhub/web/security.py:21-22`,
   `src/skillhub/web/security.py:118-124`).
8. Committed oracles подтверждают safe missing root и валидное имя битого
   шестого каталога, но S4 UI отдельно не проверяет hashed identifier для
   hostile directory name; это покрыто S2, код которого в диапазоне не менялся
   (`tests/test_skills_catalog.py:332-380`;
   `src/skillhub/registry/_registry.py:193-201`).

## Проверка диапазона

- `git merge-base e4feefa f2c6b9f` → `e4feefa`.
- Диапазон содержит один commit
  `f2c6b9f feat: добавить каталог пяти скиллов`.
- `git diff --check e4feefa..f2c6b9f` → успешно.
- `git diff` для production/tests `skillhub.registry` пуст: принятые S2/S3
  implementation и attack-oracles не изменены.
- Все семь новых skill/reference entries в target commit — обычные blobs
  mode `100644`, не symlink.
- Полный test/quality gate не запускался. Выполнена одна read-only runtime
  attack-проба Unicode CSRF-token; она воспроизвела безопасный 500 и
  неизменный каталог.
- В рамках handoff записан только `docs/handoff/e1-s4-security.md`; код, тесты
  и commit не создавались.

Итого: P0 — 0, P1 — 0, P2 — 0, P3 — 1. AppSec gate не пройден.
