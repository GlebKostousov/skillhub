# Выжимка
Роль: logic-simple
Scope: `3bd870b..6089e28`
Вердикт: НУЖНА_ДОРАБОТКА
Находки: 1 — P0: 0; P1: 0; P2: 1; P3: 0.

Основная часть `E1-S2-LS-R1-001` закрыта: `SkillRegistry` зависит от одной
семантической файловой границы, прежняя цепочка одношаговых `_load_*`
схлопнута, пять внутренних бюджетов убраны из публичного фасада, а
низкоуровневые типы не выходят из файлового слоя. Осталась одна
несогласованность реализации этой границы: Windows-backend принимает открытый
родительский дескриптор, но для обычного потомка восстанавливает путь и начинает
обход заново от корня.

# Отчёт

## Логика / простота

- [P2] [E1-S2-LS-R2-001]
  [`src/skillhub/registry/_platform_types.py:46-57`;
  `src/skillhub/registry/_windows_io.py:224-241`;
  `src/skillhub/registry/_windows_io.py:328-348`;
  `src/skillhub/registry/_posix_io.py:62-76`] → общий контракт
  `open_child(root, parent, name)` обещает открыть непосредственного потомка
  переданного открытого `parent`. POSIX-backend соблюдает это через
  `dir_fd=parent.token`, а Windows-backend строит
  `parent.final_path / name`, преобразует его в компоненты и вызывает
  `_open_components()`, который начинает открытие с `root`. Поэтому удержание
  `parent` не определяет объект чтения на Windows: после переименования или
  замены каталога будет открыт объект из нового дерева по тому же пути, а не
  потомок удерживаемого каталога. Один семантический файловый шов имеет два
  разных смысла, хотя orchestration справедливо считает их одинаковыми →
  начинать обычное открытие с переданного `parent`, например через внутренний
  путь `_open_named(root, parent, (name,), hops=0)`; корень использовать для
  проверки и разрешения целей reparse point, не для повторного поиска
  родителя по `final_path`.

- В `SkillRegistry.load()` теперь непосредственно виден один сценарий:
  строгий resolve корня → `filesystem.open_registry()` → `_load_entries()`.
  Прежние `_load_registry → _load_resolved_root → _load_open_root →
  _load_directory_root` удалены.
- В orchestration остаются только три семантические операции файловой границы:
  `list_candidates()`, `read_skill()` и `has_additional_files()`.
  `Handle`, `final_path`, `identity`, `open_path`, `open_child` и
  `PlatformAdapter` в `_registry.py` больше не встречаются.
- Поведение E1-S2 сохранено: кандидаты упорядочиваются по
  `(casefold(name), name)`; полностью разобранный повтор имени получает
  `duplicate_name`; дефекты документа изолируются в `LoadIssue`; внутренний
  дополнительный файл даёт `has_files=True`, а внешняя цель не учитывается.
- Публичного набора внутренних бюджетов больше нет. В `__all__` сохранён только
  исходный и отдельно описанный предел одного `SKILL.md`;
  `MAX_ROOT_ENTRIES`, `MAX_SKILLS`, `MAX_TOTAL_SKILL_BYTES`,
  `MAX_AUX_ENTRIES` и `MAX_AUX_DEPTH` остаются внутри пакета.
- Поиск
  `reload|snapshot|MappingProxyType|RLock|\bLock\b|def search|def list`
  в `src/skillhub/registry` не дал совпадений. Состояние снимка, reload, поиск
  и иная логика E1-S3 не внесены.

## Голос + канон

- Термины `скилл`, `реестр`, `SKILL.md`, `has_files` и «причина пропуска»
  совпадают с `CONTEXT.md:3-14`; новых конкурирующих доменных названий нет.
- Граница `core ← registry ← web`, публичный фасад и приватные платформенные
  модули соответствуют `docs/architecture/skillhub.md:12-18,80-93`.
- Докстринги дельты написаны естественно по-русски в третьем лице:
  «скрывает», «предоставляет», «читает», «определяет». Технические слова
  `backend`, POSIX, Windows и ABI используются только как названия
  платформенной реализации.
- Канон коротких приватных модулей применён по смыслу: большой
  `_windows_io.py` описывает одну платформенную механику и не образует новый
  доменный слой. Находка относится не к его размеру, а к расхождению обещанного
  и фактического смысла `parent`.

## Дизайн

- `RegistryFilesystem` — требуемый глубокий шов: он владеет дескрипторами,
  containment, идентичностями каталогов и бюджетами обхода, а реестр получает
  только результаты уровня загрузки скилла.
- `_document.parse_document()` остаётся отдельным глубоким парсером и возвращает
  проверенные доменные поля без раскрытия YAML-механики.
- `_platform_types.py`, `_posix_io.py` и `_windows_io.py` не импортируются
  orchestration. `Handle`, `PlatformAdapter` и два узких POSIX typing-протокола
  являются внутренними ABI-деталями; публичной protocol-иерархии нет.
- Большой объём Win32-структур сам по себе оправдан платформенным API, но общий
  `PlatformAdapter.open_child()` должен иметь одинаковую дескрипторную семантику
  на обеих платформах. До устранения расхождения файловую границу нельзя считать
  внутренне цельной.

## Компоненты: list / grep

`git ls-tree -r --name-only 6089e28 src/skillhub/registry` показывает десять
файлов пакета: публичный фасад, модели, парсер, ошибки, лимиты, orchestration,
семантическую файловую границу, общий ABI-контракт и два платформенных backend.

`rg -n "^(class|def) |^@dataclass|\bProtocol\b"
src/skillhub/registry` показывает один `RegistryFilesystem`, две его внутренние
state/value-модели, один `PlatformAdapter`, `Handle`, пять Win32 ABI-структур и
два локальных POSIX typing-протокола. `rg` по
`RegistryFilesystem|PlatformAdapter|Handle|open_registry|read_skill|
list_candidates|has_additional_files` подтверждает, что `_registry.py` знает
только `RegistryFilesystem` и три семантические операции.

| Компонент | Результат list / grep | Решение |
|---|---|---|
| `SkillRegistry`, `_LoadState` | Один orchestration-фасад; `Handle` и platform API не найдены | OK: линейный сценарий и состояние одной загрузки |
| `RegistryFilesystem`, `_SkillRead`, `_WalkState` | Единственный семантический файловый слой | OK с исправлением P2: граница выбрана верно |
| `_document.parse_document` | Единственный вход разбора документа | OK unique: глубокий парсер |
| `Handle`, `PlatformAdapter` | Встречаются только в `_filesystem` и platform-модулях | HIDE: утечки в registry/public API нет |
| `_posix_io`, `_windows_io` | Два backend одного `PlatformAdapter` | REWORK: выровнять смысл открытия относительно `parent` |
| `_limits` | Пять внутренних бюджетов; один ранее существовавший внешний предел файла | OK: публичного budget-zoo нет |
| `Skill`, `LoadIssue`, `LoadReport` | Три неизменяемые value-модели, аналогов loader-моделей нет | OK unique: дополнительная иерархия не нужна |
