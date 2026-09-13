# SkillHub

[![CI](https://github.com/GlebKostousov/skillhub/actions/workflows/ci.yml/badge.svg)](https://github.com/GlebKostousov/skillhub/actions/workflows/ci.yml)
[![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2A6DB2)](https://mypy-lang.org/)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A5%2085%25-brightgreen)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Локальный ассистент на FastAPI. Вы описываете, что нужно сделать, и кладёте
текст. Приложение само выбирает один из пяти режимов: деловое сообщение,
задачи встречи, протокол, резюме или перевод.

Ключ модели не обязателен. Без `DEEPSEEK_API_KEY` сервер поднимается, а
генерация отвечает `generation_unavailable`. Ключ читается только из этой
переменной, без префикса `SKILLHUB_`. Файлы `.env` приложение не загружает.

## Запуск

Нужны [uv](https://docs.astral.sh/uv/) и Python 3.13. Из корня репозитория:

```console
uv sync --frozen
```

Ключ DeepSeek, если он есть, задайте в окружении процесса. Именованный шаблон
переменных — `.env.example`; процесс этот файл не читает.

```console
uv run python -m skillhub --port 8000
```

Это единственная поддерживаемая команда процесса: она слушает `127.0.0.1`,
отключает журнал доступа и применяет безопасные события запуска.
`skillhub.main:app` — точка для внешнего ASGI-сервера, не вторая пользовательская
команда.

После старта откройте [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

| Адрес | Назначение |
|---|---|
| `/` | Окно ассистента. Для протокола здесь же уточнения и выгрузка Word |
| `/skills` | Каталог режимов и перезагрузка реестра |
| `/usage` | Журнал расходов |
| `/settings` | Живые параметры модели |
| `/health` | Готовность процесса, `{"status":"ok"}` |

JSON-зеркала: `POST /api/assistant`, `GET /api/skills`, `GET /api/usage`,
`GET`/`POST /api/settings`.

## Режимы

Страница, API и реестр показывают их в порядке канонического имени:

| Имя | Подпись |
|---|---|
| `business-message` | Деловое сообщение |
| `meeting-action-items` | Задачи встречи |
| `meeting-protocol` | Протокол встречи |
| `text-summary` | Краткое резюме |
| `text-translation` | Перевод текста |

Каждый режим — каталог в `skills/` с `SKILL.md`. Поиск сравнивает имя и
подпись без учёта регистра. Пустой запрос возвращает все пять.

Для протокола после черновика можно ответить на вопросы и либо вставить ответы
без модели, либо собрать текст заново. Готовый протокол скачивается как Word.

## Настройки

Необязательные переменные процесса:

| Переменная | Смысл |
|---|---|
| `SKILLHUB_ENVIRONMENT` | Только `development`, `test` или `production`. По умолчанию `development` |
| `SKILLHUB_USAGE_PATH` | Файл журнала расходов. По умолчанию `usage.sqlite3` |
| `SKILLHUB_DAILY_BUDGET_NANOS` | Посевной дневной потолок в нано-USD, пока нет живого наложения |

Неверное значение или неизвестная переменная `SKILLHUB_*` останавливает запуск.

Цены моделей живут в `config/model-tariffs.yaml`, не в окружении. Поле
`source_url` совпадает с
[тарифами DeepSeek](https://api-docs.deepseek.com/quick_start/pricing);
`verified_at` — `2026-09-11`. Секреты и URL поставщика в файле тарифов нет.

Живые поля модели хранятся в `runtime-overlay.json` в рабочем каталоге
процесса. Сохранение пишет файл атомарно и без секретов. Следующий вызов
модели читает новый снимок без перезапуска. Ключ поставщика со страницы и из
JSON не принимается и не показывается.

Приложение предназначено для локальной работы: только доверенные имена хоста,
CORS выключен. Абсолютная защита от prompt injection не обещается.

## Docker

```console
docker build -t skillhub .
docker run --rm -p 8000:8000 -e DEEPSEEK_API_KEY skillhub
```

Образ слушает `0.0.0.0:8000` внутри контейнера. Ключ по-прежнему задаётся
только окружением.

## Проверка

```console
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv run --no-sync lint-imports
uv run --no-sync pytest
```

Официальный порог веточного покрытия — 85 %.

Офлайн-сценарий пяти режимов, protocol → Word, расходы и сводка eval:

```console
uv run python eval/run_demo.py --offline
```

Это локальная проверка, не задача CI.

## Документация

- [Архитектура](docs/architecture/skillhub.md)
- [Почему так устроено](docs/architecture/decision-rationale.md)
- [Модель угроз](docs/security/threat-model.md)
- [Словарь](docs/glossary.md)
- [Как участвовать](CONTRIBUTING.md)
- [Сообщить об уязвимости](SECURITY.md)

Интерфейс использует локальный Bootstrap 5.3.8.

## Лицензия

[MIT](LICENSE)
