# Оперативный план эпика E4 «Уточнения и качество протокола»

Дата: 2026-09-12
Ветка: `orch-e4`
База: `d10bed5d294f236b15dcd00af289a4c08331f9fb` (`main` после принятого E3)
Режим: параллельно с E5; orchestration worktree `.worktrees/orch-e4`
Статус: принят as-built на `orch-e4`. Код слайсов S1–S6 закрыт; канонический отчёт — этот файл.

## Понимание эпика

- As-built: `GET /protocol` показывает таблицу уточнений; `POST /protocol/clarifications` даёт список, `/answer` применяет строку, `/finalize` собирает итоговый `Protocol` без повторной модели. `verify(protocol, material)` — чистая функция. На `/protocol` порт генерации в штатном `create_app` равен `None`.
- Исходная цель: пропуски превращаются в конечный список вопросов; пользователь отвечает, пропускает или отменяет строку; обсуждение не становится решением без основания.
- Не входит: журнал расходов, тарифы, дневной бюджет, вкладка «Расходы», пакет `usage` (это E5).
- E5 параллельно владеет usage, тарифами, ledger, бюджетом и экраном расходов. Общие файлы не захватываются до явного merge-контракта.

## As-built

- Вопросы строит `build_clarifications(parse(text))` только из `—` в дате, участниках, `assignee` и `due`. Максимум — константа `MAX_CLARIFICATIONS = 10`.
- Таблица на `GET /protocol`: «Проблема / Ответ / Действия / Статус»; `Отправить`, `Пропустить`, `Отменить выбор`.
- HTTP: `POST /protocol/clarifications`, `/answer`, `/finalize`. CSRF, same-origin, JSON, 1 МиБ.
- `finalize` = `apply_answers(..., require_resolved=True)` + `verify` + `parse(render)`. Pending даёт конфликт и не меняет черновик.
- Пропуск сохраняет `—`. Cancel не затирает чужие поля: клиент держит `baseText` и повторяет ответы.
- `verify(protocol, material)` — чистая функция без `LlmGateway`. Обсуждение и «решили не X» не подтверждают пункт.
- Серверной сессии нет. Идентификаторы пересчитываются из текущего Markdown.
- `ProtocolSkillHandler` не менялся. LLM-сигнал §7c вне E4.

## До и после

| Наблюдение | Сейчас | После E4 |
|---|---|---|
| Пропуски в `Protocol` | Остаются `—`, пользователь правит Markdown руками | Конечный список вопросов с одним целевым полем |
| Таблица решений | Нет | Колонки «Проблема / Ответ / Действия / Статус»; submit / skip / cancel |
| Финализация | Нет отдельного шва | Кнопка «Сформировать итоговый протокол»; pending отклоняется; модель не вызывается |
| Пропуск | Нет | `—` сохраняется; cancel возвращает `pending` |
| Grounding | Правила есть в теле скилла, отдельного verifier нет | Неподтверждённое решение не становится фактом; конфликт отмечается или пропускается |

## Источники

- `docs/phases/epic-e4-clarifications.md`
- `docs/architecture/skillhub.md` §5b, §7c, §9
- `docs/phases/skillhub-program.md`
- `CONTEXT.md` (Clarification, финализация, заполнитель)
- `AGENTS.md`
- `skills/meeting-protocol/SKILL.md`
- `skills/meeting-protocol/references/protocol-format.md`

## Capsule: epic-v2

```yaml
capsule: epic-v2
run_id: e4
repository: C:/project/test task/.worktrees/orch-e4
base_branch: main
base_sha: d10bed5d294f236b15dcd00af289a4c08331f9fb
goal: Недостающие важные данные становятся конечным списком вопросов; после явного решения по каждой строке сервер без модели собирает проверенный итоговый Protocol.
before: Черновик и Word есть; уточнений, финализации и verifier нет; пользователь правит Markdown сам.
after: Одинаковый Protocol даёт одинаковые вопросы; пустой ответ не принимается; pending блокирует финал; skip сохраняет —; обсуждение не повышается до решения без основания.
domain_terms:
  - Clarification
  - pending
  - answered
  - skipped
  - Отменить выбор
  - финализация
  - заполнитель
  - grounding
  - discussed-is-not-decided
dependencies:
  - E3 принят на main
  - E5 идёт параллельно и не отдаёт protocol/meeting-protocol
epic_risks:
  - second-model-call-on-finalize
  - client-only-validation
  - foreign-identifier
  - extra-field-write
  - discussed-promoted-to-decision
  - e5-shared-file-collision
verify_profile:
  - ruff
  - mypy
  - import-linter
  - pytest
```

## Изоляция от E5

Запрещено захватывать:

- `src/skillhub/usage/`
- `src/skillhub/llm/`
- тарифный YAML и SQLite ledger
- вкладку и маршруты расходов
- `src/skillhub/classifier/`
- `eval/` кроме сценариев протокола, если они уже принадлежат E3

Разрешено только E4:

- `src/skillhub/protocol/`
- `src/skillhub/web/protocol_routes.py`
- `src/skillhub/web/templates/protocol.html`
- `src/skillhub/web/static/protocol.js`
- `skills/meeting-protocol/`
- новые тесты этих поверхностей, без захвата всего `tests/test_web.py`

Общий merge-контракт (последовательный rebase, не параллельная запись):

- `src/skillhub/app_factory.py`: проводка порта генерации/финализации, без usage
- `src/skillhub/web/__init__.py`: только если нужен новый реэкспорт E4
- `src/skillhub/web/templates/base.html`: навигация без вкладки расходов
- `src/skillhub/assistant/_protocol.py`: только если архитектор оставит finalize на странице `/protocol` и не потребует смены окна ассистента
- `pyproject.toml` / `uv.lock` / `.importlinter`
- `plan.md` и as-built docs

`tests/test_web.py` не является epic-long claim. Новые HTTP-проверки E4 живут в отдельных файлах.

## Решения архитектора

| ID | Решение |
|---|---|
| D-E4-01 | Уточнения и финализация на `/protocol`: `POST /protocol/clarifications`, `/answer`, `/finalize`. Ассистент не принимает ответы строк. |
| D-E4-02 | `build_clarifications` и `apply_answers` в фасаде существующего `protocol`. Новый класс module не создаётся. |
| D-E4-03 | `verify` — чистая функция `(protocol, material)` без `LlmGateway`. LLM-сигнал §7c вне E4. |
| D-E4-04 | `ProtocolSkillHandler` не меняется и не входит в claims E4. |
| D-E4-05 | Вопросы только из `—` в дате, участниках, `assignee` и `due`. Косметика не блокирует финал. Максимум 10 вопросов. |
| D-E4-06 | Серверной сессии нет. Идентификаторы пересчитывает `build_clarifications(parse(text))`. Статусы живут во вкладке и теле запроса. |
| D-E4-07 | DAG: S1; S2 и S4 после S1 параллельно; S3 после S2 и S4. |

## Слайсы

1. `E4-S1` — детерминированные вопросы из полей `Protocol`.
2. `E4-S2` — таблица решений на `/protocol`.
3. `E4-S4` — grounding, verifier и сложные транскрипции.
4. `E4-S3` — серверная финализация без повторной модели.
5. `E4-S5` — интеграционные замки маршрутов и тело скилла.
6. `E4-S6` — epic rework: «решили не X» не подтверждает X; единая оболочка ошибки разбора.

DAG: `E4-S1 → E4-S2`; `E4-S1 → E4-S4`; `E4-S3` после `E4-S2` и `E4-S4`; затем `E4-S5` и `E4-S6`.

## Проверка эпика

- `ruff`, `mypy`, `import-linter`, `pytest` с порогом покрытия 85%.
- Property-based: комбинация ответить / пропустить / отменить не меняет чужие поля.
- Финализация при `pending` не меняет черновик.
- Атакующие фрагменты внутри транскрипции не повышают статус обсуждения.

## Правило движения

- Один writer на worktree.
- Shared-файлы правит только короткий sequential merge.
- E5 не получает file claims на `protocol/` и `skills/meeting-protocol/`.
