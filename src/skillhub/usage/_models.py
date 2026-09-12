"""Определяет неизменяемые значения тарифов и снимка цен."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from skillhub.usage._errors import UnknownModelError

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@dataclass(frozen=True, slots=True)
class RateCard:
    """Неизменяемые цены за миллион токенов в USD.

    Attributes:
        cache_hit: цена попадания во входной кэш.
        cache_miss: цена промаха входного кэша.
        output: цена выходных токенов.
    """

    cache_hit: Decimal
    cache_miss: Decimal
    output: Decimal


@dataclass(frozen=True, slots=True)
class PeakWindow:
    """Полуинтервал пикового окна в UTC.

    Attributes:
        start: начало окна включительно.
        end: конец окна исключительно.
    """

    start: time
    end: time


@dataclass(frozen=True, slots=True)
class PeakSchedule:
    """Расписание пиковых окон в UTC.

    Attributes:
        days: английские имена дней недели.
        intervals: полуинтервалы внутри суток.
    """

    days: tuple[str, ...]
    intervals: tuple[PeakWindow, ...]


@dataclass(frozen=True, slots=True)
class ModelTariff:
    """Неизменяемый тариф одной модели.

    Attributes:
        model: идентификатор модели.
        max_tokens: верхняя граница выходных токенов.
        timeout: таймаут вызова в секундах.
        temperature: температура выборки.
        peak: цены пикового окна.
        off_peak: цены вне пикового окна.
        peak_windows: расписание пиковых окон.
    """

    model: str
    max_tokens: int
    timeout: int
    temperature: int
    peak: RateCard
    off_peak: RateCard
    peak_windows: PeakSchedule


@dataclass(frozen=True, slots=True)
class TariffSnapshot:
    """Зафиксированные цены выбранного окна для расчёта.

    Attributes:
        model: идентификатор модели.
        cache_hit: цена попадания во входной кэш.
        cache_miss: цена промаха входного кэша.
        output: цена выходных токенов.
    """

    model: str
    cache_hit: Decimal
    cache_miss: Decimal
    output: Decimal


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """Зафиксированная строка журнала одного вызова модели.

    Attributes:
        request_id: идентификатор запроса из контекста логов.
        operation: операция вызова или unspecified.
        skill: выбранный режим или его отсутствие.
        model: идентификатор модели.
        prompt_tokens: число входных токенов.
        completion_tokens: число выходных токенов.
        total_tokens: суммарное число токенов.
        cache_hit: число входных токенов с попаданием в кэш.
        cache_miss: число входных токенов без попадания в кэш.
        reasoning: число токенов рассуждения или его отсутствие.
        cost_nanos: стоимость в целых нано-USD на момент записи.
        currency: валюта снимка тарифа.
        tariff_cache_hit: цена попадания в кэш из снимка.
        tariff_cache_miss: цена промаха кэша из снимка.
        tariff_output: цена выходных токенов из снимка.
        tariff_source: публичный источник цен.
        tariff_verified_at: дата проверки источника.
        status: состояние строки журнала.
        created_at: момент записи в UTC.
    """

    request_id: str
    operation: str
    skill: str | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit: int
    cache_miss: int
    reasoning: int | None
    cost_nanos: int
    currency: str
    tariff_cache_hit: Decimal
    tariff_cache_miss: Decimal
    tariff_output: Decimal
    tariff_source: str
    tariff_verified_at: date
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """Фактические счётчики токенов одного вызова.

    Attributes:
        cache_hit: число входных токенов с попаданием в кэш.
        cache_miss: число входных токенов без попадания в кэш.
        output: число выходных токенов.
    """

    cache_hit: int
    cache_miss: int
    output: int


@dataclass(frozen=True, slots=True)
class TariffCatalog:
    """Неизменяемый каталог тарифов одной загрузки.

    Attributes:
        currency: валюта цен, только USD.
        unit: единица цены, миллион токенов.
        effective_from: дата начала действия.
        source_url: публичный источник цен.
        verified_at: дата проверки источника.
        models: тарифы в порядке файла.
    """

    currency: str
    unit: str
    effective_from: date
    source_url: str
    verified_at: date
    models: tuple[ModelTariff, ...]

    def model(self, name: str) -> ModelTariff:
        """Возвращает тариф указанной модели.

        Args:
            name: идентификатор модели из каталога.

        Returns:
            Неизменяемый тариф модели.

        Raises:
            UnknownModelError: модели нет в этой загрузке.
        """
        return _tariff_by_name(self._index(), name)

    def snapshot(self, name: str, at: datetime) -> TariffSnapshot:
        """Собирает снимок цен на указанный момент UTC.

        Args:
            name: идентификатор модели из каталога.
            at: момент выбора пикового или внепикового окна.

        Returns:
            Неизменяемый снимок цен.
        """
        tariff = self.model(name)
        rates = _rates_at(tariff, at)
        return TariffSnapshot(
            model=tariff.model,
            cache_hit=rates.cache_hit,
            cache_miss=rates.cache_miss,
            output=rates.output,
        )

    def _index(self) -> Mapping[str, ModelTariff]:
        return {item.model: item for item in self.models}


def _tariff_by_name(index: Mapping[str, ModelTariff], name: str) -> ModelTariff:
    try:
        return index[name]
    except KeyError:
        raise UnknownModelError from None


def _rates_at(tariff: ModelTariff, at: datetime) -> RateCard:
    if _is_peak(tariff.peak_windows, at):
        return tariff.peak
    return tariff.off_peak


def _is_peak(schedule: PeakSchedule, at: datetime) -> bool:
    moment = _as_utc(at)
    if _WEEKDAY_NAMES[moment.weekday()] not in schedule.days:
        return False
    return _in_intervals(moment.timetz().replace(tzinfo=None), schedule.intervals)


def _as_utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        return at.replace(tzinfo=UTC)
    return at.astimezone(UTC)


def _in_intervals(moment: time, intervals: tuple[PeakWindow, ...]) -> bool:
    return any(_in_window(moment, window) for window in intervals)


def _in_window(moment: time, window: PeakWindow) -> bool:
    return window.start <= moment < window.end
