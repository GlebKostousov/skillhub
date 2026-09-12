"""Переводит цены и токены в целые нано-USD."""

from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Final

from skillhub.usage._errors import InvalidPriceError
from skillhub.usage._models import RateCard, TokenCounts

NANOS_PER_USD: Final[Decimal] = Decimal(1000000000)
TOKENS_PER_MILLION: Final[Decimal] = Decimal(1000000)
_NANO_QUANTUM: Final[Decimal] = Decimal(1)


def decimal_price(value: object) -> Decimal:
    """Возвращает цену как Decimal без вещественного приближения.

    Args:
        value: строка или целое из конфигурации.

    Returns:
        Неотрицательная цена в USD.

    Raises:
        InvalidPriceError: значение вещественное, отрицательное или пустое.
    """
    amount = _decimal_from_config(value)
    if amount < 0:
        raise InvalidPriceError
    return amount


def usage_nanos(tokens: TokenCounts, rates: RateCard) -> int:
    """Считает стоимость в нано-USD с одним округлением.

    Args:
        tokens: фактические счётчики токенов.
        rates: цены за миллион токенов выбранного окна.

    Returns:
        Целые нано-USD после одного `ROUND_HALF_EVEN`.
    """
    nanos = (
        _nanos_for(tokens.cache_hit, rates.cache_hit)
        + _nanos_for(tokens.cache_miss, rates.cache_miss)
        + _nanos_for(tokens.output, rates.output)
    )
    return int(nanos.quantize(_NANO_QUANTUM, rounding=ROUND_HALF_EVEN))


def reserve_nanos(input_tokens: int, output_tokens: int, peak: RateCard) -> int:
    """Считает worst-case резерв в нано-USD с округлением вверх.

    Args:
        input_tokens: верхняя оценка входных токенов.
        output_tokens: верхняя граница выходных токенов.
        peak: пиковые цены за миллион токенов.

    Returns:
        Целые нано-USD после одного `ROUND_CEILING`.
    """
    nanos = _nanos_for(input_tokens, peak.cache_miss) + _nanos_for(
        output_tokens, peak.output
    )
    return int(nanos.quantize(_NANO_QUANTUM, rounding=ROUND_CEILING))


def _decimal_from_config(value: object) -> Decimal:
    if type(value) is float:
        raise InvalidPriceError
    return _decimal_from_exact(value)


def _decimal_from_exact(value: object) -> Decimal:
    if type(value) is int:
        return Decimal(value)
    if type(value) is str:
        return _decimal_from_text(value)
    raise InvalidPriceError


def _decimal_from_text(value: str) -> Decimal:
    try:
        return Decimal(value)
    except ArithmeticError:
        raise InvalidPriceError from None


def _nanos_for(tokens: int, price: Decimal) -> Decimal:
    return Decimal(tokens) / TOKENS_PER_MILLION * price * NANOS_PER_USD
