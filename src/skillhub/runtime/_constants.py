# ruff: noqa: RUF001
"""Задаёт посев, границы и подсказки полей runtime-настроек."""

from typing import Final

OVERLAY_FILENAME: Final[str] = "runtime-overlay.json"
TEMP_PREFIX: Final[str] = ".runtime-overlay-"

SEED_MODEL: Final[str] = "deepseek-flash"
SEED_MAX_TOKENS: Final[int] = 16384
SEED_TEMPERATURE: Final[int] = 0
SEED_TIMEOUT: Final[float] = 60.0
SEED_STREAM: Final[bool] = False
SEED_THINKING: Final = "enabled"
SEED_REASONING_EFFORT: Final = "high"
SEED_TOP_P: Final[float] = 1.0
SEED_FREQUENCY_PENALTY: Final[int] = 0
SEED_PRESENCE_PENALTY: Final[int] = 0
SEED_STOP: Final[tuple[str, ...]] = ()
SEED_RESPONSE_FORMAT: Final = "text"

THINKING_VALUES: Final[tuple[str, ...]] = ("enabled", "disabled")
REASONING_EFFORT_VALUES: Final[tuple[str, ...]] = ("low", "high", "max")
RESPONSE_FORMAT_VALUES: Final[tuple[str, ...]] = ("text", "json_object")
MIN_MAX_TOKENS: Final[int] = 0
MAX_MAX_TOKENS: Final[int] = 100000
MIN_TEMPERATURE: Final[float] = 0.0
MAX_TEMPERATURE: Final[float] = 2.0
MIN_TIMEOUT: Final[float] = 0.0
MAX_TIMEOUT: Final[float] = 600.0
MIN_TOP_P: Final[float] = 0.0
MAX_TOP_P: Final[float] = 1.0
MIN_PENALTY: Final[float] = -2.0
MAX_PENALTY: Final[float] = 2.0
MIN_DAILY_BUDGET: Final[int] = 0
MAX_DAILY_BUDGET: Final[int] = 1_000_000_000_000_000
MAX_STOP_ITEMS: Final[int] = 16
MAX_STOP_LENGTH: Final[int] = 256

EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "model",
    "max_tokens",
    "temperature",
    "timeout",
    "thinking",
    "reasoning_effort",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "response_format",
    "daily_budget_nanos",
)

FIELD_TITLES: Final[dict[str, str]] = {
    "model": "Модель",
    "max_tokens": "Максимум токенов в ответе",
    "temperature": "Свобода формулировок",
    "timeout": "Ожидание ответа",
    "thinking": "Внутренние рассуждения",
    "reasoning_effort": "Глубина рассуждения",
    "top_p": "Разнообразие слов",
    "frequency_penalty": "Штраф за повторы",
    "presence_penalty": "Штраф за зацикливание темы",
    "stop": "Стоп-фразы",
    "response_format": "Формат ответа",
    "daily_budget_nanos": "Дневной потолок расходов",
}

FIELD_CHOICE_LABELS: Final[dict[str, dict[str, str]]] = {
    "thinking": {
        "enabled": "Включены",
        "disabled": "Выключены",
    },
    "reasoning_effort": {
        "low": "Низкая",
        "high": "Обычная",
        "max": "Максимальная",
    },
    "response_format": {
        "text": "Обычный текст",
        "json_object": "Структурированный JSON",
    },
}

FIELD_HINTS: Final[dict[str, str]] = {
    "model": (
        "Какая модель отвечает на запрос. Flash быстрее и дешевле, "
        "Reasoner дольше думает над сложными формулировками."
    ),
    "max_tokens": (
        "Сколько текста модель может написать за один ответ. Больше — длиннее "
        "протоколы, но дольше и дороже. Ноль — модель сама выбирает длину."
    ),
    "temperature": (
        "Насколько ответы могут отличаться от раза к разу. Ноль держит "
        "спокойный деловой стиль, выше — больше вариаций и риск отклонений."
    ),
    "timeout": (
        "Сколько секунд ждать ответ модели. Если рассуждение не уложится, "
        "запрос оборвётся, и его можно повторить."
    ),
    "thinking": (
        "Модель сначала обдумывает задачу про себя, затем пишет ответ. "
        "Это помогает сложным протоколам, но увеличивает время и расход."
    ),
    "reasoning_effort": (
        "Насколько глубоко модель проверяет формулировки и пропуски. "
        "Выше — аккуратнее, но медленнее и дороже."
    ),
    "top_p": (
        "Насколько широкий набор слов модель рассматривает. Единица не сужает "
        "выбор. Меньше единицы отсекает редкие формулировки. Ноль в запрос "
        "не уходит: поставщик тогда использует своё значение по умолчанию."
    ),
    "frequency_penalty": (
        "Снижает повторы одних и тех же слов в длинном ответе. Имеет смысл, "
        "если протокол начинает ходить по кругу."
    ),
    "presence_penalty": (
        "Подталкивает модель к новым формулировкам, если она зациклилась на одной теме."
    ),
    "stop": (
        "Фразы, на которых модель обязана остановиться. Для обычных "
        "протоколов поле оставляют пустым."
    ),
    "response_format": (
        "Обычный текст нужен для протоколов и ответов ассистента. JSON — "
        "только если ответ затем разбирает программа, а не человек."
    ),
    "daily_budget_nanos": (
        "Дневной потолок платы в нано-долларах. Пустое значение — без лимита. "
        "После исчерпания новые запросы к модели не уходят."
    ),
}
