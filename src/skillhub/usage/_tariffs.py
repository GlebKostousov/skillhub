"""Загружает тарифный YAML и считает стоимость вызова."""

import re
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from skillhub.usage._errors import (
    InvalidCurrencyError,
    InvalidPriceError,
    TariffError,
    UnknownFieldError,
    UnknownModelError,
)
from skillhub.usage._models import (
    ModelTariff,
    PeakSchedule,
    PeakWindow,
    RateCard,
    TariffCatalog,
    TariffSnapshot,
    TokenCounts,
)
from skillhub.usage._money import decimal_price, usage_nanos

_INVALID_MODEL = "model"
_INVALID_WEEKDAY = "weekday"

_PRICE_FIELDS = frozenset({"cache_hit", "cache_miss", "output"})
_MODEL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_WEEKDAYS = frozenset(
    {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }
)


class _RateCardModel(BaseModel):
    """Разбирает цены одной тарифной зоны."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_hit: Decimal
    cache_miss: Decimal
    output: Decimal

    @field_validator("cache_hit", "cache_miss", "output", mode="before")
    @classmethod
    def parse_price(cls, value: object) -> Decimal:
        """Принимает цену только как Decimal-безопасное значение.

        Args:
            value: строка или целое из YAML.

        Returns:
            Цена в USD за миллион токенов.
        """
        return decimal_price(value)


class _PeakWindowModel(BaseModel):
    """Разбирает один полуинтервал пикового окна."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: time
    end: time


class _PeakScheduleModel(BaseModel):
    """Разбирает дни и интервалы пикового окна."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    days: tuple[str, ...]
    intervals: tuple[_PeakWindowModel, ...]

    @field_validator("days")
    @classmethod
    def parse_days(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Проверяет английские имена дней недели.

        Args:
            value: дни из конфигурации.

        Returns:
            Те же дни после проверки.
        """
        if not value or any(day not in _WEEKDAYS for day in value):
            raise ValueError(_INVALID_WEEKDAY)
        return value


class _ModelEntryModel(BaseModel):
    """Разбирает лимиты и цены одной модели."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    timeout: int = Field(gt=0)
    temperature: int = Field(ge=0)
    peak: _RateCardModel
    off_peak: _RateCardModel
    peak_windows: _PeakScheduleModel

    @field_validator("model")
    @classmethod
    def parse_model(cls, value: str) -> str:
        """Проверяет каноническое имя модели.

        Args:
            value: идентификатор из YAML.

        Returns:
            То же имя после проверки.
        """
        if _MODEL_NAME.fullmatch(value) is None:
            raise ValueError(_INVALID_MODEL)
        return value


class _TariffFileModel(BaseModel):
    """Разбирает корневой документ тарифов v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    currency: Literal["USD"]
    unit: Literal["per_million_tokens"]
    effective_from: date
    source_url: str = Field(min_length=1)
    verified_at: date
    models: tuple[_ModelEntryModel, ...] = Field(min_length=1)


def load_tariffs(path: Path) -> TariffCatalog:
    """Загружает неизменяемый каталог тарифов из YAML.

    Args:
        path: путь к файлу тарифов, заданный вызывающим кодом.

    Returns:
        Проверенный неизменяемый каталог.

    Raises:
        TariffError: файл или схема отклонены без содержимого файла.
    """
    parsed = _parse_file(_load_mapping(path))
    return _catalog_from(parsed)


def calculate_cost(tokens: TokenCounts, snapshot: TariffSnapshot) -> int:
    """Считает стоимость вызова в целых нано-USD.

    Args:
        tokens: фактические счётчики токенов.
        snapshot: зафиксированные цены выбранного окна.

    Returns:
        Целые нано-USD после одного округления.
    """
    return usage_nanos(
        tokens,
        RateCard(
            cache_hit=snapshot.cache_hit,
            cache_miss=snapshot.cache_miss,
            output=snapshot.output,
        ),
    )


def _load_mapping(path: Path) -> dict[object, object]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise TariffError from None
    return _as_mapping(loaded)


def _as_mapping(loaded: object) -> dict[object, object]:
    if type(loaded) is not dict:
        raise TariffError
    return loaded


def _parse_file(raw: dict[object, object]) -> _TariffFileModel:
    try:
        return _TariffFileModel.model_validate(raw)
    except ValidationError as exc:
        _reject_validation(exc)


def _reject_validation(exc: ValidationError) -> NoReturn:
    error = exc.errors()[0]
    _raise_for_error(str(error["type"]), error["loc"])


def _raise_for_error(kind: str, location: tuple[int | str, ...]) -> NoReturn:
    if kind == "extra_forbidden":
        raise UnknownFieldError from None
    _raise_for_location(location)


def _raise_for_location(location: tuple[int | str, ...]) -> NoReturn:
    if "currency" in location:
        raise InvalidCurrencyError from None
    _raise_for_price_or_model(location)


def _raise_for_price_or_model(location: tuple[int | str, ...]) -> NoReturn:
    if _PRICE_FIELDS.intersection(location):
        raise InvalidPriceError from None
    if "model" in location:
        raise UnknownModelError from None
    raise TariffError from None


def _catalog_from(parsed: _TariffFileModel) -> TariffCatalog:
    models = tuple(_model_from(item) for item in parsed.models)
    _reject_duplicate_names(models)
    return TariffCatalog(
        currency=parsed.currency,
        unit=parsed.unit,
        effective_from=parsed.effective_from,
        source_url=parsed.source_url,
        verified_at=parsed.verified_at,
        models=models,
    )


def _reject_duplicate_names(models: tuple[ModelTariff, ...]) -> None:
    names = [item.model for item in models]
    if len(names) != len(set(names)):
        raise UnknownModelError


def _model_from(item: _ModelEntryModel) -> ModelTariff:
    return ModelTariff(
        model=item.model,
        max_tokens=item.max_tokens,
        timeout=item.timeout,
        temperature=item.temperature,
        peak=_rates_from(item.peak),
        off_peak=_rates_from(item.off_peak),
        peak_windows=_schedule_from(item.peak_windows),
    )


def _rates_from(item: _RateCardModel) -> RateCard:
    return RateCard(
        cache_hit=item.cache_hit,
        cache_miss=item.cache_miss,
        output=item.output,
    )


def _schedule_from(item: _PeakScheduleModel) -> PeakSchedule:
    return PeakSchedule(
        days=item.days,
        intervals=tuple(
            PeakWindow(start=window.start, end=window.end) for window in item.intervals
        ),
    )
