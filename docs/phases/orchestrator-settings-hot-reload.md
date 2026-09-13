# Оперативный план эпика settings-hot-reload «Горячая замена настроек»

Дата: 2026-09-13
Ветка: `fo/settings-hot-reload`
База: `84a01ca8f9343cc291692562e79ffd595dbfd67e`
Режим: программа E1–E6 принята
Run: `settings-hot-reload`
Статус: принят as-built на `fo/settings-hot-reload`. Канонический отчёт — этот файл.

## Понимание эпика

- Цель: оператор видит и меняет живые поля без перезапуска; ключ поставщика
  на страницу не выходит.
- As-built: страница «Настройки», JSON-каталог, атомарный
  `runtime-overlay.json` в рабочем каталоге процесса, горячая замена снимка.
- Не входит: ключ, URL поставщика и имя файла наложения как поля страницы;
  стартовые `environment`, `usage_path` и ключ как живые.

## As-built

- `GET /settings` и `GET /api/settings` отдают каталог полей наложения.
- `POST /api/settings` полностью заменяет документ при CSRF, точном Origin
  и JSON не больше 1 МБ; успех той же формы.
- Файл наложения всегда называется `runtime-overlay.json` и лежит в рабочем
  каталоге процесса; пишется атомарно и без секретов.
- Ключ поставщика на странице и в JSON не принимается и не показывается.
- При старте задаются только режим окружения, путь журнала и ключ поставщика.
- Живые поля: `model`, `max_tokens`, `temperature`, `timeout`, `stream`
  (только `false`), `thinking`, `reasoning_effort`, `top_p`,
  `frequency_penalty`, `presence_penalty`, `stop`, `response_format`,
  `daily_budget_nanos`.
- `SKILLHUB_DAILY_BUDGET_NANOS` — посев до действительного overlay; живой
  потолок — `daily_budget_nanos` (его читают `/usage` и `complete()`).
  Повреждённое наложение возвращает посев.

## Проверка эпика

Родительский Run `settings-hot-reload` принят на SHA
`84a01ca8f9343cc291692562e79ffd595dbfd67e` ветки `fo/settings-hot-reload`.

## Правило движения

Без новой явной команды владельца код не писать. Push в `main` только по
явной команде владельца.
