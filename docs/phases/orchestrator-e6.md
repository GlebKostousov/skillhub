# Оперативный план эпика E6 «Защита и поставка»

Дата: 2026-09-12
Ветка: `orch-e6`
База: `812d31dddcd45c6bfc6f9daa054eb398facc6246` (`main` после принятого E5)
Режим: E1–E5 приняты; feature freeze на новые пользовательские функции
Run: `e6`
Статус: запланирован; DAG линейный S1→S2→S3→S4

## Понимание эпика

- Цель: проект воспроизводимо запускается, проходит полный quality/security/eval
  gate и готов к демонстрации. Защита показывает границы доверия и измеримые
  результаты, а не обещание «модель невозможно атаковать».
- Не входит: новые режимы, ручной выбор скилла, CLI, постоянное хранение
  транскрипций, PostgreSQL/Redis/очереди, CRUD тарифов, абсолютная защита от
  prompt injection.

## До и после

| Наблюдение | Сейчас | После E6 |
|---|---|---|
| XSS, CSRF, oversized, path traversal, unknown handler | Меры есть в E1–E5, проверки размазаны по слайсам | Отдельные тесты каждого класса; ошибки без путей, prompt и ключа |
| Prompt injection | Архитектура описывает `eval/security_eval_cases.md` и `eval/run_eval.py`, в дереве их нет | Корпус атак с отчётом pass/fail; атака не получает инструменты, не выбирает обработчик после классификации и не обходит parser |
| Поставка | Локальный `uv sync --frozen`; CI и Docker отсутствуют | Полный CI, audit/secret gate, non-root multi-stage образ без ключа и dev-зависимостей |
| Демонстрация | Сценарии описаны, отдельного e2e-скрипта нет (`CONTEXT.md`) | Короткий повторяемый сценарий пяти режимов, protocol→DOCX, usage и сводка eval |

## Источники

- `docs/phases/epic-e6-security-delivery.md`
- `docs/phases/skillhub-program.md`
- `docs/security/threat-model.md`
- `docs/architecture/skillhub.md` §7d, §10
- `docs/architecture/decision-rationale.md`
- `plan.md`, `CONTEXT.md`, `AGENTS.md`, `README.md`

## Capsule: epic-v2

```yaml
capsule: epic-v2
run_id: e6
repository: C:/project/test task/.worktrees/orch-e6
base_branch: main
base_sha: 812d31dddcd45c6bfc6f9daa054eb398facc6246
goal: Локальный и CI gate зелёный; пять режимов различаются; prompt injection не получает полномочий; Word собирается безопасно; cold start повторяется только по README.
before: E1–E5 приняты. CSRF, TrustedHost, CSP, лимиты тела, файловая граница реестра и allowlist обработчиков уже есть. Атакующие проверки разрознены. eval содержит только classifier_matrix.md. CI, Docker и e2e-скрипт демонстрации отсутствуют.
after: XSS/CSRF/oversize/path traversal/unknown handler имеют отдельные тесты; adversarial корпус даёт pass/fail без инструментов и смены обработчика; чистое окружение ставится и стартует по README; контейнер без ключа и dev-зависимостей; каждый тезис защиты имеет тест, метрику или документированное ограничение.
sources:
  - path: AGENTS.md
    hash: 40d87a8d5824f51ba845bd47ebfcd72666609eee0e1e1c1a2a65ec175245c032
    excerpt: Документация и интерфейс на русском; первая строка Python-документации — глагол в третьем лице или именная конструкция.
  - path: docs/phases/epic-e6-security-delivery.md
    hash: d6ba4d1f3987178c29ae61c4cf815b2e4d371cb39218def3c5ff5e0ea410a2cd
    excerpt: Четыре слайса — границы, adversarial корпус, воспроизводимая поставка, демонстрация. Новые функции ради демо после feature freeze не добавляются.
  - path: docs/phases/skillhub-program.md
    hash: 866d713f99c9c6c21c428289703504342c3b032a026a401b128473c8ed89dca6
    excerpt: Приёмка E6 — полный локальный и CI gate; пять режимов различаются; injection не получает полномочий; Word безопасен; cold start по README.
  - path: docs/security/threat-model.md
    hash: f579a9ced129a9835c2dba6295097dd1c85bd3f3b8cfab06663fa0d8dc17ba7f
    excerpt: Модель без инструментов; материал не классифицирует; ответ модели недоверенный. Security-gate — secret scan, pip-audit, атакующие тесты, CSP/DOCX, живой injection eval.
  - path: docs/architecture/skillhub.md
    hash: fadc87456c6b620d48fd074a8353366c4048b78145f14324d8702f869e2d42bc
    excerpt: eval/ — инструменты, не импортируется рантаймом. Заявлены run_eval.py и security_eval_cases.md. CSP default-src 'self'.
  - path: plan.md
    hash: 0c4c9b577e6ff109179cf019e10e9a622985ccb6b20993d70f3b8ca47b75035c
    excerpt: E5 принят. E6 открывается только по явной команде владельца. Порог покрытия 85 %.
domain_terms:
  - CSRF
  - TrustedHost
  - CSP
  - prompt injection
  - adversarial corpus
  - security eval
  - cold start
  - feature freeze
dependencies:
  - E1–E5 приняты на main 812d31dddcd45c6bfc6f9daa054eb398facc6246
epic_risks:
  - new-user-feature-after-freeze
  - missing-dedicated-attack-tests
  - promised-absolute-injection-immunity
  - eval-imported-by-runtime
  - docker-contains-key-or-dev-deps
  - ci-absent-or-weaker-than-local
verify_profile:
  - ruff
  - mypy
  - import-linter
  - pytest
```

## Решения архитектора

| ID | Решение |
|---|---|
| D-E6-01 | Новый runtime-пакет не создаётся. Усиливаются web, registry, assistant и соседние швы. Класс инструментов — корневой `eval/`, не `skillhub.eval` и не `skillhub.security`. |
| D-E6-02 | Корпус — `eval/security_eval_cases.md`, runner — `eval/run_eval.py`. Рантайм не импортирует `eval`. Офлайн-eval в pytest/CI; живой eval не условие merge. |
| D-E6-03 | CI — надмножество локального gate плюс audit/secret scan. Docker — поставка, не замена тестов. |
| D-E6-04 | Образ multi-stage, non-root, healthcheck `GET /health`. CMD — `skillhub.main:app`, bind `0.0.0.0` в сети контейнера. CLI по-прежнему `127.0.0.1`. TrustedHost не расширяется. |
| D-E6-05 | Атаки — `tests/security/`; eval — `tests/eval/`; поставка — `tests/delivery/`. `tests/test_web.py` и E1 QA не переписываются. |
| D-E6-06 | DAG: `E6-S1 → E6-S2 → E6-S3 → E6-S4`. Shared без exclusive claim: `app_factory.py`, `pyproject.toml`, `uv.lock`, `README.md`, `.importlinter`, `docs/security/threat-model.md` до S4. |
| D-E6-07 | Немедленный security specialist — review-роль в S1 и S2. Authn/authz не добавляется. |
| D-E6-08 | Ошибки — `SkillHubError.public_message` и оболочка `{"error":{"code","message"}}`. Второй конверт не вводится. |
| D-E6-09 | Feature freeze. S4 не добавляет режимы, CLI, хранение транскрипций, ручной выбор скилла и CRUD тарифов. |

## Слайсы

Зависимы: `E6-S1 → E6-S2 → E6-S3 → E6-S4`.

1. `E6-S1` — защита границ; capsule `docs/phases/capsules/e6-s1.md`.
2. `E6-S2` — adversarial корпус; capsule `docs/phases/capsules/e6-s2.md`.
3. `E6-S3` — CI и Docker; capsule `docs/phases/capsules/e6-s3.md`.
4. `E6-S4` — демонстрация и документы защиты; capsule `docs/phases/capsules/e6-s4.md`.
5. `E6-S5` — format-lock пяти файлов для зелёного `ruff format --check`; разблокирует UR-E6-S3-01.

## Правило движения

- Один writer на worktree.
- Главный агент не пишет product-код.
- Push в `main` только по явной команде владельца.
- Feature freeze: новые пользовательские функции не входят в E6.
