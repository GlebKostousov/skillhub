"""Собирает страницу расходов и JSON-журнал без пользовательского текста."""

from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, TypedDict

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from structlog.contextvars import get_contextvars

from skillhub.core import SkillHubError
from skillhub.llm import LlmGateway, LlmRequest, LlmResult
from skillhub.usage import UsageEvent, UsageLedger


class CurrentUsage(TypedDict):
    """Публичные поля стоимости выбранного запроса."""

    request_id: str
    cost_nanos: int
    currency: str


class TodayUsage(TypedDict):
    """Агрегат committed-строк за текущую UTC-дату."""

    date: str
    committed_nanos: int
    reserved_nanos: int
    limit_nanos: int | None
    currency: str


class UsageEntry(TypedDict):
    """Публичная строка журнала без пользовательского текста."""

    request_id: str
    operation: str
    skill: str | None
    model: str
    cost_nanos: int
    currency: str
    status: str
    created_at: str


class UsageReport(TypedDict):
    """Общий снимок расходов для HTML и JSON."""

    current: CurrentUsage | None
    today: TodayUsage
    entries: list[UsageEntry]


class LoggingLlmGateway(LlmGateway):
    """Пишет диагностический исход complete() без пользовательского текста."""

    def __init__(self, inner: LlmGateway, ledger: UsageLedger) -> None:
        """Сохраняет внутренний шлюз и журнал для чтения стоимости.

        Args:
            inner: шлюз, который выполняет вызов модели.
            ledger: журнал committed-строк текущего процесса.
        """
        self._inner = inner
        self._ledger = ledger

    def complete(self, request: LlmRequest) -> LlmResult:
        """Выполняет вызов модели и пишет короткий исход в журнал логов.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат внутреннего шлюза.
        """
        started = monotonic()
        status = "ok"
        try:
            return self._inner.complete(request)
        except SkillHubError as exc:
            status = exc.code
            raise
        finally:
            _log_complete(self._ledger, status, started)


class _UsageRoutes:
    """Связывает тонкие HTTP-обработчики расходов с журналом."""

    __slots__ = ("_daily_budget_nanos", "_ledger", "_templates")

    def __init__(
        self,
        templates: Jinja2Templates,
        ledger: UsageLedger,
        daily_budget_nanos: int | None,
    ) -> None:
        """Сохраняет шаблоны, журнал и дневной потолок.

        Args:
            templates: шаблоны серверных страниц.
            ledger: журнал committed-строк текущего процесса.
            daily_budget_nanos: дневной лимит в нано-USD или его отсутствие.
        """
        self._templates = templates
        self._ledger = ledger
        self._daily_budget_nanos = daily_budget_nanos

    def usage_json(
        self,
        request_id: Annotated[str | None, Query()] = None,
    ) -> UsageReport:
        """Возвращает снимок расходов в формате JSON.

        Args:
            request_id: идентификатор выбранного запроса или его отсутствие.
        """
        return build_usage_report(
            self._ledger,
            request_id,
            self._daily_budget_nanos,
        )

    def usage_page(
        self,
        request: Request,
        request_id: Annotated[str | None, Query()] = None,
    ) -> HTMLResponse:
        """Показывает страницу «Расходы» из того же снимка, что и JSON.

        Args:
            request: входящий HTTP-запрос страницы.
            request_id: идентификатор выбранного запроса или его отсутствие.
        """
        report = build_usage_report(
            self._ledger,
            request_id,
            self._daily_budget_nanos,
        )
        return self._templates.TemplateResponse(
            request=request,
            name="usage.html",
            context=dict(report),
        )


def create_usage_router(
    templates: Jinja2Templates,
    *,
    ledger: UsageLedger,
    daily_budget_nanos: int | None,
) -> APIRouter:
    """Собирает маршрутизатор страницы и JSON расходов.

    Args:
        templates: шаблоны серверных страниц.
        ledger: журнал committed-строк текущего процесса.
        daily_budget_nanos: дневной лимит в нано-USD или его отсутствие.

    Returns:
        Маршрутизатор с двумя маршрутами чтения.
    """
    router = APIRouter()
    routes = _UsageRoutes(templates, ledger, daily_budget_nanos)
    router.add_api_route("/api/usage", routes.usage_json, methods=["GET"])
    router.add_api_route(
        "/usage",
        routes.usage_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    return router


def build_usage_report(
    ledger: UsageLedger,
    request_id: str | None,
    daily_budget_nanos: int | None,
) -> UsageReport:
    """Собирает общий снимок расходов узкими запросами к журналу.

    Args:
        ledger: журнал committed-строк.
        request_id: идентификатор выбранного запроса или его отсутствие.
        daily_budget_nanos: дневной лимит в нано-USD или его отсутствие.

    Returns:
        Снимок для HTML и JSON.
    """
    today = datetime.now(UTC).date()
    committed_nanos, reserved_nanos = ledger.day_totals(today)
    return {
        "current": _current(ledger, request_id),
        "today": {
            "date": today.isoformat(),
            "committed_nanos": committed_nanos,
            "reserved_nanos": reserved_nanos,
            "limit_nanos": daily_budget_nanos,
            "currency": "USD",
        },
        "entries": [_entry(event) for event in ledger.recent()],
    }


def _current(
    ledger: UsageLedger,
    request_id: str | None,
) -> CurrentUsage | None:
    if request_id is None or request_id == "":
        return None
    matches = ledger.lookup(request_id)
    if not matches:
        return None
    return {
        "request_id": request_id,
        "cost_nanos": _sum_cost(matches),
        "currency": matches[0].currency,
    }


def _entry(event: UsageEvent) -> UsageEntry:
    return {
        "request_id": event.request_id,
        "operation": event.operation,
        "skill": event.skill,
        "model": event.model,
        "cost_nanos": event.cost_nanos,
        "currency": event.currency,
        "status": event.status,
        "created_at": event.created_at.isoformat(),
    }


def _log_complete(ledger: UsageLedger, status: str, started: float) -> None:
    request_id = _bound_request_id()
    structlog.get_logger(__name__).info(
        "usage.complete",
        request_id=request_id,
        duration_ms=int((monotonic() - started) * 1000),
        status=status,
        cost_nanos=_committed_cost(ledger, request_id, status),
    )


def _committed_cost(ledger: UsageLedger, request_id: str, status: str) -> int:
    if status != "ok":
        return 0
    return _sum_cost(ledger.lookup(request_id))


def _sum_cost(events: tuple[UsageEvent, ...]) -> int:
    return sum(event.cost_nanos for event in events)


def _bound_request_id() -> str:
    value = get_contextvars().get("request_id")
    if type(value) is str:
        return value
    return ""
