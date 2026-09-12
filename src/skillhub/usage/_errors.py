"""Определяет типизированные отказы загрузки и расчёта тарифов."""

from skillhub.core import SkillHubError


class TariffError(SkillHubError):
    """Описывает отказ принять конфигурацию тарифов."""

    code = "invalid_tariff"
    status_code = 400
    public_message = "Конфигурация тарифов отклонена."


class UnknownFieldError(TariffError):
    """Описывает неизвестное поле в конфигурации тарифов."""

    code = "unknown_field"
    public_message = "Конфигурация тарифов содержит неизвестное поле."


class UnknownModelError(TariffError):
    """Описывает модель, которой нет в загруженном каталоге."""

    code = "unknown_model"
    public_message = "Тариф запрошенной модели отсутствует."


class InvalidPriceError(TariffError):
    """Описывает отрицательную или вещественную цену."""

    code = "invalid_price"
    public_message = "Цена тарифа отклонена."


class InvalidCurrencyError(TariffError):
    """Описывает валюту, отличную от USD."""

    code = "invalid_currency"
    public_message = "Валюта тарифа должна быть USD."


class LedgerError(SkillHubError):
    """Описывает отказ открыть или записать журнал расходов."""

    code = "invalid_ledger"
    status_code = 500
    public_message = "Журнал расходов недоступен."


class SchemaVersionError(LedgerError):
    """Описывает несовместимую версию схемы журнала."""

    code = "unsupported_schema"
    public_message = "Версия журнала расходов не поддерживается."


class DailyBudgetExceededError(SkillHubError):
    """Описывает отказ начать вызов сверх дневного лимита."""

    code = "daily_budget_exceeded"
    status_code = 429
    public_message = "Дневной лимит модельных расходов исчерпан"
