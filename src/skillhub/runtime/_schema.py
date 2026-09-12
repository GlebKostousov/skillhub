"""Проверяет JSON-схему наложения runtime-настроек."""

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from skillhub.runtime._constants import (
    MAX_PENALTY,
    MAX_STOP_ITEMS,
    MAX_STOP_LENGTH,
    MAX_TEMPERATURE,
    MAX_TIMEOUT,
    MAX_TOP_P,
    MIN_DAILY_BUDGET,
    MIN_MAX_TOKENS,
    MIN_PENALTY,
    MIN_TEMPERATURE,
    MIN_TIMEOUT,
    MIN_TOP_P,
    SEED_FREQUENCY_PENALTY,
    SEED_MAX_TOKENS,
    SEED_MODEL,
    SEED_PRESENCE_PENALTY,
    SEED_REASONING_EFFORT,
    SEED_RESPONSE_FORMAT,
    SEED_STOP,
    SEED_STREAM,
    SEED_TEMPERATURE,
    SEED_THINKING,
    SEED_TIMEOUT,
    SEED_TOP_P,
)
from skillhub.runtime._errors import InvalidOverlayError
from skillhub.runtime._models import OverlayValues

_INVALID_STOP = "stop"
_INVALID_MODEL = "model"
_INVALID_MAX_TOKENS = "max_tokens"


class _OverlayModel(BaseModel):
    """Разбирает полный документ runtime-наложения."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str
    max_tokens: int = Field(ge=MIN_MAX_TOKENS)
    temperature: int | float = Field(ge=MIN_TEMPERATURE, le=MAX_TEMPERATURE)
    timeout: float = Field(gt=MIN_TIMEOUT, le=MAX_TIMEOUT)
    stream: Literal[False]
    thinking: Literal["enabled", "disabled"]
    reasoning_effort: Literal["low", "high", "max"]
    top_p: float = Field(ge=MIN_TOP_P, le=MAX_TOP_P)
    frequency_penalty: int | float = Field(ge=MIN_PENALTY, le=MAX_PENALTY)
    presence_penalty: int | float = Field(ge=MIN_PENALTY, le=MAX_PENALTY)
    stop: tuple[str, ...] = Field(max_length=MAX_STOP_ITEMS)
    response_format: Literal["text", "json_object"]
    daily_budget_nanos: int | None = Field(ge=MIN_DAILY_BUDGET)

    @field_validator("stop", mode="before")
    @classmethod
    def parse_stop(cls, value: object) -> tuple[str, ...]:
        """Принимает ограниченный список непустых строк.

        Args:
            value: список или кортеж стоп-последовательностей.

        Returns:
            Проверенный кортеж строк.
        """
        items = _as_string_tuple(value)
        if len(items) > MAX_STOP_ITEMS:
            raise ValueError(_INVALID_STOP)
        if any(not item or len(item) > MAX_STOP_LENGTH for item in items):
            raise ValueError(_INVALID_STOP)
        return items

    @field_validator("model")
    @classmethod
    def parse_model(cls, value: str, info: ValidationInfo) -> str:
        """Проверяет модель по allowlist вызывающего кода.

        Args:
            value: идентификатор модели.
            info: контекст с картой лимитов.

        Returns:
            То же имя после проверки.
        """
        if value not in _limits_from(info):
            raise ValueError(_INVALID_MODEL)
        return value

    @model_validator(mode="after")
    def parse_ceiling(self, info: ValidationInfo) -> Self:
        """Проверяет max_tokens против потолка выбранной модели.

        Args:
            info: контекст с картой лимитов.

        Returns:
            Та же модель после проверки потолка.
        """
        if self.max_tokens > _limits_from(info)[self.model]:
            raise ValueError(_INVALID_MAX_TOKENS)
        return self


def seed_values(daily_budget_nanos: int | None) -> OverlayValues:
    """Собирает посевные значения редактируемых полей.

    Args:
        daily_budget_nanos: посевной дневной потолок или его отсутствие.

    Returns:
        Посевной набор значений.
    """
    return OverlayValues(
        model=SEED_MODEL,
        max_tokens=SEED_MAX_TOKENS,
        temperature=SEED_TEMPERATURE,
        timeout=SEED_TIMEOUT,
        stream=SEED_STREAM,
        thinking=SEED_THINKING,
        reasoning_effort=SEED_REASONING_EFFORT,
        top_p=SEED_TOP_P,
        frequency_penalty=SEED_FREQUENCY_PENALTY,
        presence_penalty=SEED_PRESENCE_PENALTY,
        stop=SEED_STOP,
        response_format=SEED_RESPONSE_FORMAT,
        daily_budget_nanos=daily_budget_nanos,
    )


def parse_overlay(raw: object, model_limits: Mapping[str, int]) -> OverlayValues:
    """Проверяет документ наложения и возвращает типизированные значения.

    Args:
        raw: разобранный JSON-объект.
        model_limits: допустимые модели и потолок max_tokens.

    Returns:
        Проверенные значения наложения.

    Raises:
        InvalidOverlayError: схема, ключ или диапазон отклонены.
    """
    if type(raw) is not dict:
        raise InvalidOverlayError
    try:
        parsed = _OverlayModel.model_validate(raw, context={"limits": model_limits})
    except ValidationError:
        raise InvalidOverlayError from None
    return OverlayValues(
        model=parsed.model,
        max_tokens=parsed.max_tokens,
        temperature=parsed.temperature,
        timeout=parsed.timeout,
        stream=parsed.stream,
        thinking=parsed.thinking,
        reasoning_effort=parsed.reasoning_effort,
        top_p=parsed.top_p,
        frequency_penalty=parsed.frequency_penalty,
        presence_penalty=parsed.presence_penalty,
        stop=parsed.stop,
        response_format=parsed.response_format,
        daily_budget_nanos=parsed.daily_budget_nanos,
    )


def overlay_payload(values: OverlayValues) -> dict[str, object]:
    """Собирает JSON-документ из проверенных значений.

    Args:
        values: типизированные значения наложения.

    Returns:
        Документ для атомарной записи.
    """
    return {
        "model": values.model,
        "max_tokens": values.max_tokens,
        "temperature": values.temperature,
        "timeout": values.timeout,
        "stream": values.stream,
        "thinking": values.thinking,
        "reasoning_effort": values.reasoning_effort,
        "top_p": values.top_p,
        "frequency_penalty": values.frequency_penalty,
        "presence_penalty": values.presence_penalty,
        "stop": list(values.stop),
        "response_format": values.response_format,
        "daily_budget_nanos": values.daily_budget_nanos,
    }


def _as_string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is list:
        items = tuple(value)
    elif type(value) is tuple:
        items = value
    else:
        raise ValueError(_INVALID_STOP)
    if any(type(item) is not str for item in items):
        raise ValueError(_INVALID_STOP)
    return items


def _limits_from(info: ValidationInfo) -> Mapping[str, int]:
    context = info.context or {}
    limits: Mapping[str, int] = context["limits"]
    return limits
