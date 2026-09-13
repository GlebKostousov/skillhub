"""Записывает стоимость успешного вызова модели в журнал."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from structlog.contextvars import get_contextvars

from skillhub.llm._constants import DEEPSEEK_MODEL
from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult, LlmUsage
from skillhub.runtime import OverlayValues, RuntimeStore
from skillhub.usage import (
    LedgerError,
    ModelTariff,
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
SnapshotSource = RuntimeStore | Callable[[], OverlayValues]


class MeteredLlmGateway(LlmGateway):
    """Оборачивает шлюз и фиксирует расход после успешного complete()."""

    def __init__(
        self,
        inner: LlmGateway,
        ledger: UsageLedger,
        catalog: TariffSource,
        daily_budget_nanos: int | None = None,
        store: SnapshotSource | None = None,
    ) -> None:
        """Сохраняет внутренний шлюз, журнал, тарифы и источник снимка.

        Args:
            inner: шлюз, который выполняет вызов модели.
            ledger: журнал расходов по переданному пути.
            catalog: готовый каталог или функция повторной загрузки.
            daily_budget_nanos: дневной лимит без снимка или его отсутствие.
            store: хранилище снимка или функция его чтения.
        """
        self._inner = inner
        self._ledger = ledger
        self._catalog = catalog
        self._daily_budget_nanos = daily_budget_nanos
        self._store = store

    def complete(self, request: LlmRequest) -> LlmResult:
        """Резервирует лимит при потолке и фиксирует исход вызова.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат внутреннего шлюза.
        """
        catalog = _resolve(self._catalog)
        values = _clamped_values(_snapshot_values(self._store), catalog)
        reservation = self._try_reserve(request, values)
        return self._finish(request, reservation, values)

    def _try_reserve(
        self,
        request: LlmRequest,
        values: OverlayValues | None,
    ) -> int | None:
        budget = _budget(values, self._daily_budget_nanos)
        if budget is None:
            return None
        catalog = _resolve(self._catalog)
        return self._ledger.reserve(_reserved_event(request, catalog, values), budget)

    def _finish(
        self,
        request: LlmRequest,
        reservation: int | None,
        values: OverlayValues | None,
    ) -> LlmResult:
        try:
            result = _invoke(self._inner, request, values)
        except Exception:
            self._release(reservation)
            raise
        return self._persist(reservation, result, values)

    def _persist(
        self,
        reservation: int | None,
        result: LlmResult,
        values: OverlayValues | None,
    ) -> LlmResult:
        try:
            self._commit(reservation, result, values)
        except Exception:
            self._release(reservation)
            raise
        return result

    def _release(self, reservation: int | None) -> None:
        if reservation is None:
            return
        self._ledger.release(reservation)

    def _commit(
        self,
        reservation: int | None,
        result: LlmResult,
        values: OverlayValues | None,
    ) -> None:
        event = _committed_event(result, _resolve(self._catalog), values)
        if reservation is None:
            self._ledger.record(event)
            return
        self._ledger.commit(
            reservation,
            event,
            _budget(values, self._daily_budget_nanos),
        )


def _resolve(source: TariffSource) -> TariffCatalog:
    if isinstance(source, TariffCatalog):
        return source
    return source()


def _invoke(
    inner: LlmGateway,
    request: LlmRequest,
    values: OverlayValues | None,
) -> LlmResult:
    hook = getattr(inner, "complete_with_values", None)
    if values is not None and callable(hook):
        typed = cast("Callable[[LlmRequest, OverlayValues], LlmResult]", hook)
        return typed(request, values)
    return inner.complete(request)


def _snapshot_values(store: SnapshotSource | None) -> OverlayValues | None:
    if store is None:
        return None
    if isinstance(store, RuntimeStore):
        return store.snapshot().values
    return store()


def _clamped_values(
    values: OverlayValues | None,
    catalog: TariffCatalog,
) -> OverlayValues | None:
    if values is None:
        return None
    tariff = catalog.model(values.model)
    ceiling = min(values.max_tokens, tariff.max_tokens)
    if ceiling == values.max_tokens:
        return values
    return replace(values, max_tokens=ceiling)


def _budget(values: OverlayValues | None, fallback: int | None) -> int | None:
    if values is None:
        return fallback
    return values.daily_budget_nanos


def _reserved_event(
    request: LlmRequest,
    catalog: TariffCatalog,
    values: OverlayValues | None,
) -> UsageEvent:
    now = datetime.now(UTC)
    tariff = _tariff(catalog, values)
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


def _committed_event(
    result: LlmResult,
    catalog: TariffCatalog,
    values: OverlayValues | None,
) -> UsageEvent:
    now = datetime.now(UTC)
    snapshot = catalog.snapshot(_model_name(catalog, values), now)
    usage = result.usage
    _reject_negative_usage(usage)
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


def _tariff(catalog: TariffCatalog, values: OverlayValues | None) -> ModelTariff:
    tariff = catalog.model(_model_name(catalog, values))
    if values is None:
        return tariff
    return replace(tariff, max_tokens=min(values.max_tokens, tariff.max_tokens))


def _model_name(catalog: TariffCatalog, values: OverlayValues | None) -> str:
    if values is None:
        return _catalog_model(catalog)
    return catalog.model(values.model).model


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


def _reject_negative_usage(usage: LlmUsage) -> None:
    tokens = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    if any(item < 0 for item in tokens):
        raise LedgerError
