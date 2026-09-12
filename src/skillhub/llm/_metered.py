"""Записывает стоимость успешного вызова модели в журнал."""

from collections.abc import Callable
from datetime import UTC, datetime

from structlog.contextvars import get_contextvars

from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult
from skillhub.usage import (
    TariffCatalog,
    TokenCounts,
    UsageEvent,
    UsageLedger,
    bound_call,
    calculate_cost,
)

TariffSource = TariffCatalog | Callable[[], TariffCatalog]


class MeteredLlmGateway(LlmGateway):
    """Оборачивает шлюз и фиксирует расход после успешного complete()."""

    def __init__(
        self,
        inner: LlmGateway,
        ledger: UsageLedger,
        catalog: TariffSource,
    ) -> None:
        """Сохраняет внутренний шлюз, журнал и источник тарифов.

        Args:
            inner: шлюз, который выполняет вызов модели.
            ledger: журнал расходов по переданному пути.
            catalog: готовый каталог или функция повторной загрузки.
        """
        self._inner = inner
        self._ledger = ledger
        self._catalog = catalog

    def complete(self, request: LlmRequest) -> LlmResult:
        """Вызывает внутренний шлюз и при успехе пишет committed-строку.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат внутреннего шлюза.
        """
        result = self._inner.complete(request)
        self._ledger.record(_committed_event(result, _resolve(self._catalog)))
        return result


def _resolve(source: TariffSource) -> TariffCatalog:
    if isinstance(source, TariffCatalog):
        return source
    return source()


def _committed_event(result: LlmResult, catalog: TariffCatalog) -> UsageEvent:
    now = datetime.now(UTC)
    snapshot = catalog.snapshot(catalog.models[0].model, now)
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


def _request_id() -> str:
    value = get_contextvars().get("request_id")
    if type(value) is str:
        return value
    return ""
