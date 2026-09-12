# Выжимка
Роль: perf
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 1 — P2 PERF-001: рекурсивная зачистка не ограничивает глубину/объём и повторно разворачивает общие ссылки.

# Отчёт
Рецепт: да

## Анализ

Проверен фактический diff `22bf7d9..ce9390f` для E1-S1: рекурсивная обработка structured logs, middleware и handlers, template/static I/O и сборка приложения.

- [P2] [PERF-001] [src/skillhub/core/logging.py:_redact_value] → time O(Nexp + L) для обычного дерева, но O(2^n) относительно числа уникальных контейнеров `n` на DAG вида `x = [x, x]` по уровням; цикл или глубина выше лимита Python завершаются `RecursionError` | memory O(Nexp + d), в худшем случае O(2^n), где `d` — глубина стека | n = число уникальных `dict/list/tuple`, Nexp = число посещений после разворачивания ссылок, L = суммарная длина проверяемых ключей; bounds отсутствуют → любой structured event проходит глобальный processor, поэтому рост payload/context раздувает CPU, копию события и итоговый лог до обработки ошибки → заменить рекурсию на итеративный graph-aware обход с учётом `id`, маркером для циклических/повторных ссылок и жёсткими лимитами глубины, узлов и итоговых байтов; после исчерпания бюджета обрезать поддерево стабильным маркером.

## Сводка complexity

| Путь / операция | O(time) | O(memory) | Bound на n? | Вердикт |
|---|---:|---:|---|---|
| `core/logging.py:_is_sensitive_field` — нормализация ключа; set из 10 полей и 4 suffix | O(k) | O(k) временно | `k` не ограничен API; текущие ключи handlers константны | OK |
| `core/logging.py:redact_sensitive_fields/_redact_value` — обход `dict/list/tuple` | O(Nexp + L), worst O(2^n) по уникальным узлам | O(Nexp + d), worst O(2^n) | Нет лимита узлов/глубины; циклы не поддержаны | PERF-001 |
| `core/logging.py:configure_logging` — список processors | O(p) | O(p) | `p = 5` | OK |
| `core/logging.py:structlog renderer + stdout I/O` | O(B + p) | O(B) | `p = 5`; `B` общего события не ограничен, но два текущих error-event имеют фиксированные поля | OK при устранении PERF-001 |
| `core/logging.py:bind_request_id/clear_request_context` | O(c) | O(1) дополнительно | Явно привязан один ключ; `c` — число ContextVars текущего контекста | OK |
| `core/settings.py:Settings` — чтение process environment при старте | O(v + f) | O(v + f) | `f = 1`, `.env` I/O отключён; `v` задаётся окружением процесса | OK |
| `app_factory.py:create_app` — списки routes/middleware/handlers и registrations | O(r + m + e) | O(r + m + e) | `r = 7` итоговых routes (4 FastAPI + static + 2 прикладных), `m = 2`, `e = 2` добавленных handlers | OK |
| `app_factory.py:StaticFiles(...)` — startup directory check | O(q) + один metadata I/O | O(q) | `q` — длина фиксированного пути; один каталог | OK |
| `TrustedHostMiddleware` — scan `allowed_hosts` | O(a · k) | O(1) | `a = 3`, `k` — длина Host | OK |
| `SecurityHeadersMiddleware.dispatch` — middleware chain и update headers | O(m + h) сверх downstream | O(m + h) | `m = 2`, `h = 5` headers | OK |
| Router dispatch / reverse lookup из двух `url_for` | O(r · q) / O(2r) | O(1) | `r = 7`; при росте числа handlers поиск линейный, текущий bound мал и фиксирован | OK |
| `web/errors.py` — установка и lookup exception handlers, JSON/log payload | startup O(e), request O(u + J) | O(J) | `e = 2`; `u` — глубина MRO ошибки; `J` фиксирован контрактом | OK |
| `web/routes.py:root` + `home.html` — template read/parse/render | cold/change O(T + r), cached O(T + r) с metadata check | O(T) | `T = 991` bytes, циклов шаблона нет, 2 `url_for`, динамического списка нет | OK |
| `StaticFiles` response — stat/open/read/send | O(P + S) | O(C) при chunked send | `Smax = 232111` bytes, 2 CSS; `C` — bounded send chunk | OK |
| `core/web __all__`, sensitive suffixes, security-header literals | O(x) при создании/обходе | O(x) | Размеры фиксированы: 6, 3, 4 и 5 элементов | OK |
