"""Записывает стоимость успешного вызова модели в журнал."""

from collections.abc import Callable
from datetime import UTC, datetime

from structlog.contextvars import get_contextvars

from skillhub.llm._constants import DEEPSEEK_MODEL
from skillhub.llm._errors import GenerationUnavailableError, ProviderError
from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult
from skillhub.usage import (
    TariffCatalog,
    TokenCounts,
    UnknownModelError,
    UsageEvent,
    UsageLedger,
    bound_call,
    calculate_cost,
    calculate_reserve,
)

TariffSource = TariffCatalog | Callable[[], TariffCatalog]


class MeteredLlmGateway(LlmGateway):
    """Оборачивает шлюз и фиксирует расход после успешного complete()."""

    def __init__(
        self,
        inner: LlmGateway,
        ledger: UsageLedger,
        catalog: TariffSource,
        daily_budget_nanos: int | None = None,
    ) -> None:
        """Сохраняет внутренний шлюз, журнал, тарифы и дневной потолок.

        Args:
            inner: шлюз, который выполняет вызов модели.
            ledger: журнал расходов по переданному пути.
            catalog: готовый каталог или функция повторной загрузки.
            daily_budget_nanos: дневной лимит в нано-USD или его отсутствие.
        """
        self._inner = inner
        self._ledger = ledger
        self._catalog = catalog
        self._daily_budget_nanos = daily_budget_nanos

    def complete(self, request: LlmRequest) -> LlmResult:
        """Резервирует лимит при потолке и фиксирует исход вызова.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат внутреннего шлюза.
        """
        reservation = self._try_reserve(request)
        return self._finish(request, reservation)

    def _try_reserve(self, request: LlmRequest) -> int | None:
        budget = self._daily_budget_nanos
        if budget is None:
            return None
        catalog = _resolve(self._catalog)
        return self._ledger.reserve(_reserved_event(request, catalog), budget)

    def _finish(self, request: LlmRequest, reservation: int | None) -> LlmResult:
        try:
            result = self._inner.complete(request)
        except (ProviderError, GenerationUnavailableError):
            self._release(reservation)
            raise
        self._commit(reservation, result)
        return result

    def _release(self, reservation: int | None) -> None:
        if reservation is None:
            return
        self._ledger.release(reservation)

    def _commit(self, reservation: int | None, result: LlmResult) -> None:
        event = _committed_event(result, _resolve(self._catalog))
        if reservation is None:
            self._ledger.record(event)
            return
        self._ledger.commit(reservation, event)


def _resolve(source: TariffSource) -> TariffCatalog:
    if isinstance(source, TariffCatalog):
        return source
    return source()


def _reserved_event(request: LlmRequest, catalog: TariffCatalog) -> UsageEvent:
    now = datetime.now(UTC)
    tariff = catalog.model(_catalog_model(catalog))
    input_tokens = sum(len(message.content) for message in request.messages)
    peak = tariff.peak
    operation, skill = bound_call()
    return UsageEvent(
        request_id=_request_id(),
        operation=operation,
        skill=skill,
        model=tariff.model,
        prompt_tokens=input_tokens,
        completion_tokens=tariff.max_tokens,
        total_tokens=input_tokens + tariff.max_tokens,
        cache_hit=0,
        cache_miss=input_tokens,
        reasoning=None,
        cost_nanos=calculate_reserve(input_tokens, tariff),
        currency=catalog.currency,
        tariff_cache_hit=peak.cache_hit,
        tariff_cache_miss=peak.cache_miss,
        tariff_output=peak.output,
        tariff_source=catalog.source_url,
        tariff_verified_at=catalog.verified_at,
        status="reserved",
        created_at=now,
    )


def _committed_event(result: LlmResult, catalog: TariffCatalog) -> UsageEvent:
    now = datetime.now(UTC)
    snapshot = catalog.snapshot(_catalog_model(catalog), now)
    usage = result.usage
    operation, skill = bound_call()
    tokens = TokenCounts(
        cache_hit=0,
        cache_miss=usage.prompt_tokens,
        output=usage.completion_tokens,
    )
    return UsageEvent(
        request_id=_request_id(),
        operation=operation,
        skill=skill,
        model=snapshot.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cache_hit=0,
        cache_miss=usage.prompt_tokens,
        reasoning=None,
        cost_nanos=calculate_cost(tokens, snapshot),
        currency=catalog.currency,
        tariff_cache_hit=snapshot.cache_hit,
        tariff_cache_miss=snapshot.cache_miss,
        tariff_output=snapshot.output,
        tariff_source=catalog.source_url,
        tariff_verified_at=catalog.verified_at,
        status="committed",
        created_at=now,
    )


def _catalog_model(catalog: TariffCatalog) -> str:
    names = {item.model for item in catalog.models}
    return catalog.model(_chosen_name(names)).model


def _chosen_name(names: set[str]) -> str:
    if DEEPSEEK_MODEL in names:
        return DEEPSEEK_MODEL
    return _unique_name(names)


def _unique_name(names: set[str]) -> str:
    if len(names) != 1:
        raise UnknownModelError
    return next(iter(names))


def _request_id() -> str:
    value = get_contextvars().get("request_id")
    if type(value) is str:
        return value
    return ""
