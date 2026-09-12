# Выжимка

Роль: logic-simple
Scope: `c60d086..bd997c7`
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 2 — P0: 0; P1: 0; P2: 0; P3: 2.

Расхождение смысла `parent` между платформами закрыто: обычный потомок теперь
открывается от переданного родительского дескриптора и на POSIX, и на Windows,
а возврат к корню происходит только после разбора внутренней цели ссылки.
`RegistryFilesystem` остаётся единственной семантической файловой границей,
общий бюджет разрешения вынесен в один приватный модуль, а Windows-разделение
не вывело новые типы в фасад. Gate блокируют два малых дефекта простоты:
оставшийся без вызовов альтернативный вход `_open_components()` и
неестественная смесь русского с `handle`/`backend`/`reparse-target` в новых
докстрингах.

# Отчёт

## Логика / простота

- [P3] [E1-S2-LS-R3-001]
  [`src/skillhub/registry/_windows_resolver.py:46-52`] → после перехода
  `open_child()` на `_resolve_components(root, parent, ...)` приватная
  `_open_components(root, components, hops=0)` осталась без единого вызова во
  всём Python-коде. Она сохраняет второй способ начать тот же resolver от root
  и отдельно конструирует `ResolutionBudget(link_hops=hops)`, хотя рабочий путь
  всегда начинает бюджет через `open_child()`. Это остаток прежней схемы, а не
  самостоятельная ответственность модуля → удалить мёртвый вход и оставить
  один путь `open_child() → _resolve_components()`.

- [P3] [E1-S2-LS-R3-002]
  [`src/skillhub/registry/_windows_io.py:1`;
  `src/skillhub/registry/_windows_directory.py:1`;
  `src/skillhub/registry/_windows_handles.py:1`;
  `src/skillhub/registry/_windows_reparse.py:1`;
  `src/skillhub/registry/_windows_resolver.py:1,24,33`] → новые русские
  докстринги регулярно вставляют английские существительные без необходимости:
  «Windows backend», «удерживаемый handle», «удерживаемые handles»,
  «parent handle», «Windows reparse-target». Рядом уже используется точный и
  естественный термин «дескриптор», а канон требует русские Google-style
  docstring → заменить эти обороты на «Windows-реализация»,
  «удерживаемый/родительский дескриптор» и единообразное русское описание цели
  reparse point.

- Основная P2 из rework-2 закрыта буквально по контракту:
  `PlatformAdapter.open_child(root, parent, name)` обещает открыть
  непосредственного потомка `parent`; POSIX вызывает
  `_resolve_components(root, parent, (name,))`, Windows делает то же, и обе
  реализации открывают обычный компонент относительно текущего
  родительского дескриптора.
- Обычный потомок доверенного UNC-root больше не проходит через нормализацию
  недоверенной цели reparse point. Компоненты от root строятся только после
  фактического чтения ссылки; поэтому исправление семантики `parent`
  одновременно убирает прежний отказ обычного UNC-потомка.
- `ResolutionBudget` — один источник пределов для обеих платформ:
  посещение компонента, переход по ссылке, максимум 256 шагов и 40 переходов
  описаны только в `_resolution.py`. Платформенные resolver хранят свою очередь
  и дескрипторы, но не дублируют численные правила.
- `_registry.py` не изменён и по-прежнему видит только
  `RegistryFilesystem`, `list_candidates()`, `read_skill()` и
  `has_additional_files()`. Низкоуровневые `Handle`, `PlatformAdapter`,
  Windows ABI и resolver в оркестрацию не вышли.
- Изменение `_filesystem._is_within()` заменяет обход `Path.parents` на
  `os.path.commonpath()`: тот же containment-контракт теперь выражен одной
  операцией и корректно возвращает отказ для несопоставимых корней.
- Публичные модели, разбор `SKILL.md`, порядок кандидатов, duplicate policy,
  причины пропуска и вычисление `has_files` дельта не меняет. Поиск
  `reload|snapshot|MappingProxyType|RLock|\bLock\b|def search|def list`
  находит только прежний `list_candidates()`; состояния, reload и поиска
  E1-S3 в слайсе нет.

## Голос + канон

- Доменные слова `скилл`, `реестр`, `SKILL.md`, `has_files` и «причина
  пропуска» не переименованы и совпадают с `CONTEXT.md`.
- Граница `core ← registry ← web`, приватный файловый слой и публичный
  `SkillRegistry` соответствуют канону. Новых repository/service/plugin
  сущностей и публичных платформенных контрактов нет.
- Докстринги описывают действие в третьем лице и сохраняют Google-style
  секции `Args`, `Returns` и `Yields`. Отклонение ограничено лексикой новых
  Windows-модулей и зафиксировано как `E1-S2-LS-R3-002`.

## Дизайн

- `RegistryFilesystem` остаётся глубоким семантическим фасадом: он скрывает
  жизненный цикл дескрипторов, containment, идентичности и бюджеты обхода, а
  `SkillRegistry` получает только операции уровня загрузки скилла.
- Разделение прежнего 791-строчного `_windows_io.py` не создало новую
  иерархию фасадов. `_windows_io.py` — 11-строчная точка сборки одного
  `PlatformAdapter`; ABI, перечисление, I/O дескрипторов, разбор reparse data и
  разрешение пути имеют по одному приватному владельцу.
- `_resolution.py` оправдан как единственная общая причина изменения для
  платформенных лимитов. Платформенные state-модели остаются рядом с
  различающейся механикой ОС и не протекают наружу.
- После удаления мёртвой `_open_components()` у Windows resolver останется
  один вход и один линейный цикл разрешения; остальные функции являются
  этапами этого цикла, а не набором альтернативных helper API.

## Компоненты: list / grep

`git ls-tree -r --name-only bd997c7 src/skillhub/registry` показывает 16 файлов:
шесть прежних доменных файлов, семантическую файловую границу, общий
низкоуровневый контракт, два платформенных backend и шесть приватных деталей
разрешения/Windows-реализации.

`rg -n "^(@dataclass[^\n]*|class |def )" src/skillhub/registry` показывает один
`SkillRegistry`, один `RegistryFilesystem`, один `PlatformAdapter`, один
`ResolutionBudget`, по одной приватной state-модели resolver на платформу,
пять Win32 ABI-структур и функции с локальными обязанностями. Поиск
`_open_components\(` находит только её определение; поиск
`RegistryFilesystem|PlatformAdapter|Handle|open_registry|read_skill|
list_candidates|has_additional_files` подтверждает, что orchestration знает
только семантический фасад.

| Компонент | Результат list / grep | Решение |
|---|---|---|
| `SkillRegistry`, `_LoadState` | Один orchestration-фасад; platform API не используется | OK: договор E1-S2 не расширен |
| `RegistryFilesystem`, `_SkillRead`, `_WalkState` | Один семантический файловый слой и три операции для orchestration | OK: глубокая граница сохранена |
| `Handle`, `PlatformAdapter` | Только `_filesystem` и платформенные модули | HIDE: публичной утечки нет |
| `ResolutionBudget` | Один класс и два потребителя — POSIX/Windows | OK unique: общий источник пределов |
| `_posix_io` | Один backend со своим descriptor resolver | OK: смысл `parent` совпадает с общим контрактом |
| `_windows_io` | Только сборка пяти операций адаптера | OK unique: не второй фасад |
| `_windows_abi` | Константы, функции ABI и пять структур | OK: одна причина изменения |
| `_windows_handles` | Открытие, чтение, metadata и закрытие дескрипторов | OK: одна низкоуровневая механика |
| `_windows_directory` | Перечисление открытого каталога | OK unique: изолирует directory buffer |
| `_windows_reparse` | Разбор буфера и нормализация цели | OK unique: чистая логика цели |
| `_windows_resolver` | Один state machine, но два входа; `_open_components()` не вызывается | REWORK P3: удалить мёртвый альтернативный вход |
| Новые Windows-докстринги | Пять файлов содержат `handle`/`backend`/`reparse-target` в русском тексте | REWORK P3: привести к естественному русскому |
