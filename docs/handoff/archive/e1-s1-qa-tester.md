# Выжимка
Роль: qa-tester
Вердикт: НУЖНА_ДОРАБОТКА
Находки:
- [P1] [access-log-query-leak] [README.md:11] → штатная команда оставляет uvicorn access log включённым, и полный query с пользовательским содержимым попадает в stdout → отключить access log либо установить безопасный formatter/filter без raw target.
- [P1] [startup-config-value-leak] [src/skillhub/main.py:5] → неверное значение известной настройки печатается вместе с traceback Pydantic при старте → перехватывать ошибку конфигурации на process boundary и писать только безопасный код отказа.
- [P1] [config-unknown-env] [src/skillhub/core/settings.py:17] → неизвестная переменная `SKILLHUB_*` молча игнорируется, поэтому опечатка включает значения по умолчанию вместо fail-closed → отклонять неизвестные переменные с префиксом `SKILLHUB_` при сборке настроек.
- [P1] [framework-error-envelope] [src/skillhub/web/errors.py:74] → framework-ответы 400/404/405 обходят единый типизированный SkillHub envelope: 400 возвращает text/plain, 404/405 — `{"detail": ...}` → централизованно нормализовать HTTP/framework-ошибки в безопасные коды `bad_request`, `not_found`, `method_not_allowed`.
- [P1] [log-protocol-leak] [src/skillhub/core/logging.py:12] → рекурсивный scrubber не считает поле `protocol` чувствительным и пишет содержимое протокола в JSON-лог → добавить канонические поля пользовательского содержимого в централизованную зачистку.
- [P2] [unexpected-error-correlation] [src/skillhub/web/security.py:59] → контекст очищается до логирования неожиданной ошибки, поэтому событие `http.unexpected_error` нельзя связать с `X-Request-ID` ответа → сохранять или повторно привязывать request ID до записи события и очищать после неё.
- [P2] [git-ignore-agent-artifact] [.gitignore:41] → локальный каталог `.serena/` остаётся в `git status`, хотя раздел заявляет исключение agent artifacts → добавить `.serena/` и подтвердить через `git check-ignore`.

# Отчёт
Рецепт: да
Договор: `docs/phases/orchestrator-e1.md`, раздел `E1-S1`.

Оракулы стоят на публичных seams: `Settings`, HTTP через `TestClient`, отдельный процесс из README, Git plumbing и импортируемый пакет `core`. Пирамида оставлена с преобладанием unit/HTTP integration и одним процессным e2e; матрица покрывает happy, deny, invalid, domain, replay и attack, а не опирается на один coverage.

## Матрица рисков

| Риск | Seam и независимый оракул | Результат |
|---|---|---|
| Success root/health | Точное HTML-содержание `/`, точный JSON `/health`, реальный процесс из единственной README-команды | Пройдено |
| Failure error/log | Точные безопасные ответы expected/unexpected, отсутствие входа и traceback, совпадение request ID ответа и лога, отдельный startup process | Не пройдено: access log раскрывает query; startup раскрывает значение и traceback; unexpected log без request ID |
| Invalid config/404 | Неверное значение и лишний `SKILLHUB_*` через `Settings`; единый типизированный envelope для 400/404/405 | Не пройдено: лишняя env-переменная игнорируется, неверное значение раскрывается при старте, framework-ошибки имеют чужой envelope |
| Domain core boundary | AST-страж разрешает `core` только собственный пакет; import-linter запрещает `core → web/composition` | Пройдено |
| Replay app/health | Два приложения и повторные health-запросы дают неизменный контракт и разные request ID | Пройдено |
| Attack secret/CORS/bind | Рекурсивный scrubber, hostile Host/Origin, отсутствие CORS, localhost bind и запуск без provider env | Не пройдено: `protocol` и query утекают в логи |
| Git ignore/EOL/config | `check-ignore`, `check-attr`, `ls-files --eol`, локальные Git-настройки | Не пройдено: `.serena/` не исключён; EOL и local config корректны |

## Аудит исходных тестов

- Happy-path root/health проверял содержимое, а не только статус; security headers, hostile Host/Origin, 404 и безопасные ошибки уже имели полезные HTTP-оракулы.
- `extra` проверялся через `model_validate`, но не через настоящий источник `BaseSettings`; тест не ловил неизвестную `SKILLHUB_*`.
- Unexpected-error тест проверял отсутствие утечки, но не корреляцию машинного лога с ответом.
- Scrubber проверял рекурсию для `authorization`, но happy-only список имён не включал канонический `protocol`.
- Не было тестов README-процесса, startup/access-логов, Git ignore/EOL, будущей границы `core`, а также повторного app/health.
- Проверки 400/404/405 ранее ограничивались статусом и отсутствием утечки, поэтому пропускали чужие `text/plain`/`{"detail": ...}`; добавлен структурный оракул SkillHub envelope с точными машинными кодами без привязки к формулировке сообщения.
- Привязки к private-функциям и mock-only оракулов не обнаружено; точные HTML/JSON проверки относятся к публичному контракту.

## Изменено в tests

- Добавлен `tests/test_readme_startup.py`: извлечение единственной команды из README, localhost bind, старт отдельного процесса без `DEEPSEEK_*`/`SKILLHUB_*`, точный `/health`, отсутствие raw query и значения неверной конфигурации в process logs.
- Добавлен `tests/test_repository_contract.py`: ignore-матрица, `.env.example`, EOL-атрибуты, LF индекса и динамический запрет `core` импортировать прикладные пакеты.
- Добавлен `tests/test_http_error_contract.py`: единый типизированный SkillHub envelope и машинные коды для framework-ответов 400/404/405.
- Усилены `tests/test_logging.py`, `tests/test_settings.py`, `tests/test_web.py`: nested protocol redaction, unknown env fail-closed, unexpected-error correlation и replay двух приложений.

## Команды и exit

- `uv sync` → `0`.
- `uv run ruff check .` → `0`.
- `uv run ruff format --check .` → `0`.
- `uv run mypy src tests` → `0`, 18 файлов.
- `uv run lint-imports` → `0`, 1 контракт соблюдён.
- `uv run pytest` → `1`, 9 failed / 46 passed; три параметра framework-envelope и ещё шесть дефектов соответствуют семи находкам.
- `git diff --check` → `0`.
- `git ls-files --eol` → `0`: все отслеживаемые текстовые файлы имеют `i/lf`, политика задаёт `eol=lf`.
- `git config --local --list --show-origin` → `0`: `core.autocrlf=false`, `core.safecrlf=true`, `fetch.prune=true`, `pull.ff=only`, `push.autosetupremote=true`, источник — repo-local `.git/config`.
