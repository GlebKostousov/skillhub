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
STREAM_VALUES: Final[tuple[bool, ...]] = (False,)

MIN_MAX_TOKENS: Final[int] = 1
MIN_TEMPERATURE: Final[float] = 0.0
MAX_TEMPERATURE: Final[float] = 2.0
MIN_TIMEOUT: Final[float] = 0.0
MAX_TIMEOUT: Final[float] = 600.0
MIN_TOP_P: Final[float] = 0.0
MAX_TOP_P: Final[float] = 1.0
MIN_PENALTY: Final[float] = -2.0
MAX_PENALTY: Final[float] = 2.0
MIN_DAILY_BUDGET: Final[int] = 0
MAX_STOP_ITEMS: Final[int] = 16
MAX_STOP_LENGTH: Final[int] = 256

EDITABLE_FIELDS: Final[tuple[str, ...]] = (
    "model",
    "max_tokens",
    "temperature",
    "timeout",
    "stream",
    "thinking",
    "reasoning_effort",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "response_format",
    "daily_budget_nanos",
)

FIELD_HINTS: Final[dict[str, str]] = {
    "model": "Идентификатор модели из разрешённого каталога.",
    "max_tokens": "Верхняя граница числа выходных токенов для выбранной модели.",
    "temperature": "Температура выборки ответа модели.",
    "timeout": "Таймаут вызова модели в секундах.",
    "stream": "Потоковая передача ответа; допускается только выключенное состояние.",
    "thinking": "Режим внутреннего рассуждения модели.",
    "reasoning_effort": "Глубина рассуждения модели.",
    "top_p": "Порог ядерной выборки токенов.",
    "frequency_penalty": "Штраф за повтор уже выбранных токенов.",
    "presence_penalty": "Штраф за уже встреченные темы.",
    "stop": "Список стоп-последовательностей или пустой список.",
    "response_format": "Ожидаемый формат ответа модели.",
    "daily_budget_nanos": (
        "Дневной потолок расходов в нано-USD либо отсутствие потолка."
    ),
}
