"""Определяет замороженные значения и каталог полей runtime-настроек."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from skillhub.runtime._constants import (
    EDITABLE_FIELDS,
    FIELD_HINTS,
    MAX_PENALTY,
    MAX_STOP_ITEMS,
    MAX_TEMPERATURE,
    MAX_TIMEOUT,
    MAX_TOP_P,
    MIN_DAILY_BUDGET,
    MIN_MAX_TOKENS,
    MIN_PENALTY,
    MIN_TEMPERATURE,
    MIN_TIMEOUT,
    MIN_TOP_P,
    REASONING_EFFORT_VALUES,
    RESPONSE_FORMAT_VALUES,
    STREAM_VALUES,
    THINKING_VALUES,
)


@dataclass(frozen=True, slots=True)
class OverlayValues:
    """Проверенные значения редактируемых runtime-настроек.

    Attributes:
        model: идентификатор модели из allowlist вызывающего кода.
        max_tokens: верхняя граница выходных токенов.
        temperature: температура выборки.
        timeout: таймаут вызова в секундах.
        stream: потоковая передача; только выключенное состояние.
        thinking: режим рассуждения модели.
        reasoning_effort: глубина рассуждения.
        top_p: порог ядерной выборки.
        frequency_penalty: штраф за повтор токенов.
        presence_penalty: штраф за уже встреченные темы.
        stop: стоп-последовательности.
        response_format: ожидаемый формат ответа.
        daily_budget_nanos: дневной потолок в нано-USD или его отсутствие.
    """

    model: str
    max_tokens: int
    temperature: int | float
    timeout: float
    stream: bool
    thinking: Literal["enabled", "disabled"]
    reasoning_effort: Literal["low", "high", "max"]
    top_p: float
    frequency_penalty: int | float
    presence_penalty: int | float
    stop: tuple[str, ...]
    response_format: Literal["text", "json_object"]
    daily_budget_nanos: int | None


@dataclass(frozen=True, slots=True)
class OverlayField:
    """Одно поле замороженного каталога runtime-настроек.

    Attributes:
        name: имя редактируемого поля.
        value: текущее значение после посева или файла.
        default: посевное значение поля.
        hint: русская подсказка для страницы настроек.
        kind: машинный вид поля.
        allowed: допустимые значения выбора или их отсутствие.
        min: нижняя граница числа или её отсутствие.
        max: верхняя граница числа или её отсутствие.
    """

    name: str
    value: object
    default: object
    hint: str
    kind: str
    allowed: tuple[object, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Замороженный каталог runtime-настроек и их типизированные значения.

    Attributes:
        values: проверенные текущие значения.
        fields: каталог полей с подсказками и границами.
    """

    values: OverlayValues
    fields: tuple[OverlayField, ...]

    def __iter__(self) -> Iterator[OverlayField]:
        """Даёт поля каталога в фиксированном порядке.

        Returns:
            Итератор полей снимка.
        """
        return iter(self.fields)


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """Описывает вид и границы одного поля каталога."""

    kind: str
    allowed: tuple[object, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None


def catalog_fields(
    values: OverlayValues,
    defaults: OverlayValues,
    model_limits: Mapping[str, int],
) -> tuple[OverlayField, ...]:
    """Собирает замороженный каталог полей снимка.

    Args:
        values: текущие значения после посева или файла.
        defaults: посевные значения тех же полей.
        model_limits: допустимые модели и потолок max_tokens.

    Returns:
        Каталог редактируемых полей в фиксированном порядке.
    """
    specs = _specs_for(values.model, model_limits)
    return tuple(
        OverlayField(
            name=name,
            value=getattr(values, name),
            default=getattr(defaults, name),
            hint=FIELD_HINTS[name],
            kind=spec.kind,
            allowed=spec.allowed,
            min=spec.min,
            max=spec.max,
        )
        for name, spec in specs.items()
    )


def _specs_for(model: str, model_limits: Mapping[str, int]) -> dict[str, _FieldSpec]:
    specs = {
        "model": _FieldSpec("choice", allowed=tuple(model_limits)),
        "max_tokens": _FieldSpec(
            "integer",
            min=MIN_MAX_TOKENS,
            max=model_limits.get(model),
        ),
        "temperature": _FieldSpec("number", min=MIN_TEMPERATURE, max=MAX_TEMPERATURE),
        "timeout": _FieldSpec("number", min=MIN_TIMEOUT, max=MAX_TIMEOUT),
        "stream": _FieldSpec("boolean", allowed=STREAM_VALUES),
        "thinking": _FieldSpec("choice", allowed=THINKING_VALUES),
        "reasoning_effort": _FieldSpec("choice", allowed=REASONING_EFFORT_VALUES),
        "top_p": _FieldSpec("number", min=MIN_TOP_P, max=MAX_TOP_P),
        "frequency_penalty": _FieldSpec("number", min=MIN_PENALTY, max=MAX_PENALTY),
        "presence_penalty": _FieldSpec("number", min=MIN_PENALTY, max=MAX_PENALTY),
        "stop": _FieldSpec("string_list", max=MAX_STOP_ITEMS),
        "response_format": _FieldSpec("choice", allowed=RESPONSE_FORMAT_VALUES),
        "daily_budget_nanos": _FieldSpec("integer", min=MIN_DAILY_BUDGET),
    }
    return {name: specs[name] for name in EDITABLE_FIELDS}
