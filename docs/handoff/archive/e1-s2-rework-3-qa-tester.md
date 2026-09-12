# Выжимка

Роль: qa-tester
Scope: `c60d086..bd997c7`
Вердикт: НУЖНА_ДОРАБОТКА
Findings: 1 — P0: 0, P1: 0, P2: 1, P3: 0.

Held-parent relative open подтверждён настоящим Windows rename/replacement
сценарием. Итеративный resolver соблюдает общий бюджет 256 шагов и отдельный
предел 40 переходов, не накапливает промежуточные дескрипторы. Обычный потомок
доверенного UNC-root открывается относительно принятого parent handle, а
недоверенная UNC reparse-target отклоняется до content-open. Разделение
Windows ABI, handles, directory enumeration, reparse parser и resolver
сохранило прежние инварианты E1-S2.

Gate блокирует новый точечный parser oracle: mount-point buffer с нечётным
`SubstituteNameOffset=1` принимается и декодируется как `C:\x`, хотя
структурно неверное смещение UTF-16 должно завершаться fail-closed.
Финальный результат: 170 passed, 1 failed, 4 platform skips; branch coverage
88,87 % при пороге 85 %. `GATE_OK` не выставлен.

# Отчёт

Рецепт: да.

## Договор, scope и релевантные findings

Проверен договор E1-S2 из
`docs/phases/orchestrator-e1.md`, раздел «Безопасная валидация файлового
реестра». Сверены QA rework-2 и findings rework-2 reviewer/perf/security/
logic-simple, затем появившиеся параллельные rework-3 handoff reviewer,
security, perf и logic-simple.

QA оценивал committed delta `c60d086..bd997c7`. Изменены только
`tests/test_registry.py` и этот handoff. Production, зависимости, lock-файл и
commit не менялись. Параллельные rework-3 handoff других ролей не являются
изменениями QA.

Reviewer отдельно трактует абсолютную внутреннюю UNC reparse-target как
разрешённую. В данном QA gate проверена сформулированная владельцем граница:
configured UNC-root и обычный child доверены, строка UNC из reparse buffer
недоверена и отклоняется до открытия. Поэтому reviewer finding не
дублируется как QA finding.

## Оракулы

- Публичный oracle: полный набор `SkillRegistry.load()` над временными
  деревьями подтверждает модели, причины пропуска, порядок, лимиты, YAML,
  containment, duplicate first-wins, `has_files` и безопасные поля отчёта.
- Native Windows oracle: после rename удерживаемого каталога и создания
  замены по прежнему pathname `open_child()` читает исходный файл через
  родительский дескриптор, не replacement.
- Capability oracle: нативные Windows-вызовы подменяются только для
  детерминированного наблюдения parent, no-follow, запрета content-open,
  cumulative budget и lifecycle дескрипторов.
- Чистый parser oracle: бинарный `REPARSE_DATA_BUFFER` проверяется без
  файловой системы и без platform skip.

## Матрица рисков

| Риск | Независимый oracle | Результат |
|---|---|---|
| Held-parent swap | Реальный Windows rename/replacement; читается original marker, replacement marker отсутствует | Пройдено |
| Root swap и final handle | Реальная junction-подмена и подставленный внешний root handle не доходят до enumeration/read | Пройдено |
| Cumulative 256 | Две link expansion через production `open_child()`; overflow на 257-м резервируемом шаге до открытия новой очереди | Пройдено |
| Link hops 40 | Ровно 40 `follow_link()` разрешены, 41-й отклонён | Пройдено |
| Итеративность/resources | 129 промежуточных handles закрыты; peak не выше двух одновременно созданных resolver handles | Пройдено |
| Trusted UNC ordinary child | Probe и content open получают удерживаемый UNC parent и непосредственное имя | Пройдено |
| Untrusted UNC reparse | Только исходный `link` является reparse; обход policy немедленно достигает запрещённого content-open | Пройдено |
| Windows module split | ABI, handles, directory, parser и resolver импортируются раздельно; public registry API не расширен | Пройдено |
| Reparse malformed buffer | Bounds, odd length, bad UTF-16 и empty target отклонены; odd name offset принят | **Не пройдено** |
| Прежние S2-инварианты | Все прежние тесты проходят; остаются только четыре заранее известные platform skips | Пройдено |
| Coverage gate | Финальные 88,87 % branch coverage при требовании 85 % | Пройдено, но общий gate заблокирован finding |

## Finding

### [P2] [E1-S2-R3-QA-001] Нечётный SubstituteNameOffset принимается как UTF-16 target

Файлы:

- `src/skillhub/registry/_windows_reparse.py:116-143`;
- `tests/test_registry.py`, параметр odd-offset в
  `test_windows_reparse_parser_rejects_malformed_buffers`.

`_validate_reparse_name_bounds()` проверяет чётность `name_length` и конец
диапазона, но не чётность `name_offset`. Payload с mount-point
`SubstituteNameOffset=1`, `SubstituteNameLength=8` и байтами
`xC\x00:\x00\\\x00x\x00` проходит bounds check. Parser начинает декодирование
со второго байта и возвращает `("C:\\x", False)` вместо
`UnsafePathError`.

Это нарушает fail-closed договор разбора повреждённого reparse buffer и
позволяет интерпретировать байты вне корректной границы `WCHAR` как доверенную
структуру цели. Последующий containment всё ещё ограничивает итоговый путь
корнем, поэтому severity — P2, не P1.

Минимальное исправление: перед decode отклонять нечётный
`SubstituteNameOffset` так же, как нечётную длину, не ослабляя существующие
bounds/UTF-16 checks. Добавленный regression oracle уже фиксирует требуемый
результат.

## Mutant audit

In-memory mutations не записывали production-файлы:

- замена Windows parent на root убита trusted-UNC/held-parent oracle;
- удаление `require_pending()` убито cumulative test: вместо 129 открытий
  mutant выполняет 252;
- удаление `_close_owned()` убито равенством opened/closed и peak-oracle;
- обход `target_components()` policy сначала пережил старый UNC-тест:
  fake ошибочно объявлял reparse каждый следующий компонент, и mutant
  завершался только по hop-limit. После ограничения fake исходным `link`
  тот же mutant убит первым запрещённым content-open;
- отсутствие проверки чётности `name_offset` оказалось живым поведением
  production. Новый parser oracle воспроизводит его как единственный
  блокирующий тест.

## Аудит слабых и хрупких тестов

- Cumulative test больше не вызывает мёртвый приватный
  `_open_components()`: он входит через рабочий `open_child()`, выполняет две
  expansion и проверяет ранний отказ, точное число открытий, cleanup и peak.
- UNC-reparse oracle больше не может ложно пройти по исчерпанию 40 hops:
  reparse возвращается только для начального имени.
- Held-parent oracle использует настоящие Windows handles и не зависит от
  права создавать symlink.
- Три старых symlink smoke ожидаемо пропущены на Windows из-за WinError 1314.
  Их риски покрывают native junction и capability seams без этого skip.
- Настоящий FIFO ожидаемо POSIX-only; platform-neutral special-handle oracle
  отдельно запрещает чтение non-regular файла.
- Реальный SMB share намеренно не используется: trusted UNC проверяется через
  native-relative capability boundary, а untrusted UNC — через запрет
  content-open, без сетевого side effect.
- Новые тесты выбраны по resolver/parser рискам. Рост покрытия с 88,74 % до
  88,87 % побочный и не был целью.

## Изменения QA

- `tests/test_registry.py`:
  - укреплён UNC-reparse oracle против ложного прохождения по hop-limit;
  - cumulative test переведён на production `open_child()` и дополнен
    ранним I/O/cleanup/peak oracle;
  - добавлена точная граница 40 link hops;
  - добавлен malformed odd-offset parser case, обнаруживший P2.
- `docs/handoff/e1-s2-rework-3-qa-tester.md`: записан этот re-gate.

## Команды, coverage и skips

- Baseline `uv run --no-sync pytest -q -rs` → exit 0:
  169 passed, 4 skipped, branch coverage 88,74 %.
- Resolver/UNC/budget focus с `--no-cov` → exit 0:
  7 passed, 87 deselected.
- Malformed parser focus с `--no-cov` → exit 1:
  1 failed, 9 passed; odd-offset не вызвал `UnsafePathError`.
- Четыре in-memory mutant-run → held-parent, cumulative-budget,
  resolver-cleanup и UNC-reparse policy mutants killed.
- Финальный `uv run --no-sync pytest -q -rs` → exit 1:
  170 passed, 1 failed, 4 skipped; branch coverage 88,87 %, порог 85 %
  достигнут. Exit 1 вызван блокирующим regression oracle, не coverage.
- Skips: 3 обычных symlink smoke — WinError 1314; 1 настоящий FIFO —
  POSIX-only. Native Windows held-parent/junction и capability seams не
  skipped.
- `uv run --no-sync ruff check .` → exit 0.
- `uv run --no-sync ruff format --check .` → exit 0, 104 files.
- `uv run --no-sync mypy src tests` → exit 0, 40 source files.
- `uv run --no-sync lint-imports` → exit 0, 1 контракт соблюдён.
- `uv run --no-sync ruff check --select C90 --config
  "lint.mccabe.max-complexity=3" src/skillhub/registry` → exit 0.
- `git diff --check c60d086..bd997c7` и `git diff --check` → exit 0.

## Findings

P0: 0.

P1: 0.

P2: 1 — `E1-S2-R3-QA-001`.

P3: 0.
