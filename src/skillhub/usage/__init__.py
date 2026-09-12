"""Предоставляет публичный фасад тарифов, привязки вызова и журнала.

Каталог и путь журнала задаёт вызывающий код; пакет сам Settings не читает.
"""

from skillhub.usage._bind import bind_call, bound_call
from skillhub.usage._errors import (
    DailyBudgetExceededError,
    InvalidCurrencyError,
    InvalidPriceError,
    LedgerError,
    SchemaVersionError,
    TariffError,
    UnknownFieldError,
    UnknownModelError,
)
from skillhub.usage._ledger import UsageLedger, open_ledger
from skillhub.usage._models import (
    ModelTariff,
    RateCard,
    TariffCatalog,
    TariffSnapshot,
    TokenCounts,
    UsageEvent,
)
from skillhub.usage._tariffs import calculate_cost, calculate_reserve, load_tariffs

__all__ = [
    "DailyBudgetExceededError",
    "InvalidCurrencyError",
    "InvalidPriceError",
    "LedgerError",
    "ModelTariff",
    "RateCard",
    "SchemaVersionError",
    "TariffCatalog",
    "TariffError",
    "TariffSnapshot",
    "TokenCounts",
    "UnknownFieldError",
    "UnknownModelError",
    "UsageEvent",
    "UsageLedger",
    "bind_call",
    "bound_call",
    "calculate_cost",
    "calculate_reserve",
    "load_tariffs",
    "open_ledger",
]
