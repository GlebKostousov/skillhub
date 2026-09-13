# Как участвовать

Репозиторий принимает небольшие правки: исправления, тесты, документацию.
Новые режимы добавляются каталогом в `skills/` с `SKILL.md`, а не исполняемым
кодом.

## Окружение

Нужны Python 3.13 и [uv](https://docs.astral.sh/uv/).

```console
uv sync --frozen
```

После клонирования примените настройки только к этому репозиторию:

```console
git config --local core.autocrlf false
git config --local core.safecrlf true
git config --local fetch.prune true
git config --local pull.ff only
git config --local push.autoSetupRemote true
```

`.gitattributes` хранит текст в LF, оставляет CRLF только для `.bat` и `.cmd`.
Проверить нормализацию можно командой `git ls-files --eol`.

## Проверка

```console
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv run --no-sync lint-imports
uv run --no-sync pytest
```

Документация, комментарии и текст интерфейса пишутся по-русски. Первая строка
докстринга — глагол в третьем лице настоящего времени или именная конструкция,
не повелительное наклонение. Подробнее: [`AGENTS.md`](AGENTS.md).

## Границы

- секреты и `.env` в коммит не входят;
- URL поставщика и ключ модели не выносятся на страницу «Настройки»;
- содержимое встреч не пишется в журнал расходов;
- `SKILL.md` разбирается как данные, не импортируется и не исполняется.
