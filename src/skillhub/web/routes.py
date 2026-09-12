"""Собирает серверные страницы и каталог в формате JSON."""

from typing import Annotated, Literal, TypedDict

from anyio import Lock, WouldBlock
from anyio.lowlevel import checkpoint_if_cancelled
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from skillhub.registry import SkillRegistry
from skillhub.web._catalog import (
    SkillsPage,
    SkillSummary,
    _CatalogState,
    build_skills_page,
    public_catalog,
)
from skillhub.web._reload_guard import validate_csrf_token, validate_reload_request
from skillhub.web.errors import ReloadUnavailableError

_RELOAD_UNAVAILABLE_NOTICE = "reload-unavailable"


class HealthResponse(TypedDict):
    """Стабильный ответ проверки готовности."""

    status: Literal["ok"]


class _Routes:
    """Связывает тонкие HTTP-обработчики с зависимостями приложения."""

    __slots__ = (
        "_catalog",
        "_csrf_token",
        "_registry",
        "_reload_gate",
        "_templates",
    )

    def __init__(
        self,
        templates: Jinja2Templates,
        registry: SkillRegistry,
        csrf_token: str,
    ) -> None:
        """Сохраняет зависимости обработчиков.

        Args:
            templates: шаблоны серверных страниц.
            registry: единственный файловый реестр приложения.
            csrf_token: секрет CSRF текущего экземпляра приложения.
        """
        self._templates = templates
        self._csrf_token = csrf_token
        self._catalog = _CatalogState()
        self._registry = registry
        self._reload_gate = Lock()

    def health(self) -> HealthResponse:
        """Возвращает готовность процесса принимать запросы."""
        return {"status": "ok"}

    def root(self, request: Request) -> Response:
        """Показывает страницу готовности приложения."""
        return self._templates.TemplateResponse(request=request, name="home.html")

    def list_skills(
        self,
        q: Annotated[str, Query()] = "",
    ) -> tuple[SkillSummary, ...]:
        """Возвращает публичные метаданные результатов поиска.

        Args:
            q: искомая часть имени или подписи.

        Returns:
            Публичные карточки текущего поколения в каноническом порядке.
        """
        catalog = public_catalog(self._registry.capture())
        return catalog.search(q)

    def skills_page(
        self,
        request: Request,
        q: Annotated[str, Query()] = "",
        reload_marker: Annotated[str | None, Query(alias="reload")] = None,
        notice: Annotated[str | None, Query()] = None,
    ) -> Response:
        """Показывает страницу «Скиллы» из текущего поколения или одноразового итога."""
        flash = self._catalog.consume(reload_marker)
        catalog = flash or public_catalog(self._registry.capture())
        page = build_skills_page(
            catalog,
            q,
            self._csrf_token,
            reload_performed=flash is not None,
            reload_unavailable=notice == _RELOAD_UNAVAILABLE_NOTICE,
        )
        response = _render_skills_page(self._templates, request, page)
        if reload_marker is not None or notice is not None:
            response.headers["Cache-Control"] = "no-store"
        return response

    async def reload_skills(self, request: Request) -> Response:
        """Проверяет форму перезагрузки и перенаправляет на страницу каталога."""
        await validate_reload_request(request, self._csrf_token)
        if not _acquire_nowait(self._reload_gate):
            return _unavailable_redirect()
        try:
            return await self._perform_reload()
        finally:
            self._reload_gate.release()

    async def _perform_reload(self) -> Response:
        marker = _reserve_marker(self._catalog)
        if marker is None:
            return _unavailable_redirect()
        published = False
        try:
            report = await run_in_threadpool(self._registry.reload)
            await checkpoint_if_cancelled()
            self._catalog.publish_reload(marker, report)
            published = True
        finally:
            if not published:
                self._catalog.discard_reservation(marker)
        return RedirectResponse(
            url=f"/skills?reload={marker}",
            status_code=303,
        )


def create_router(
    templates: Jinja2Templates,
    *,
    registry: SkillRegistry,
    csrf_token: str,
) -> APIRouter:
    """Собирает публичный маршрутизатор приложения.

    Args:
        templates: шаблоны серверных страниц.
        registry: единственный файловый реестр приложения.
        csrf_token: секрет CSRF из безопасных для URL символов ASCII длиной 1–256.

    Returns:
        Маршрутизатор с пятью прикладными маршрутами.

    Raises:
        ValueError: настроенный секрет CSRF не соответствует контракту.
    """
    validate_csrf_token(csrf_token)
    router = APIRouter()
    routes = _Routes(templates, registry, csrf_token)
    router.add_api_route("/health", routes.health, methods=["GET"])
    router.add_api_route(
        "/",
        routes.root,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    router.add_api_route(
        "/api/skills",
        routes.list_skills,
        methods=["GET"],
        response_model=list[SkillSummary],
    )
    router.add_api_route(
        "/skills",
        routes.skills_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    router.add_api_route(
        "/skills/reload",
        routes.reload_skills,
        methods=["POST"],
        response_class=RedirectResponse,
        include_in_schema=False,
    )
    return router


def _acquire_nowait(lock: Lock) -> bool:
    try:
        lock.acquire_nowait()
    except WouldBlock:
        return False
    return True


def _reserve_marker(catalog: _CatalogState) -> str | None:
    try:
        return catalog.reserve_reload()
    except ReloadUnavailableError:
        return None


def _unavailable_redirect() -> RedirectResponse:
    return RedirectResponse(
        url=f"/skills?notice={_RELOAD_UNAVAILABLE_NOTICE}",
        status_code=303,
    )


def _render_skills_page(
    templates: Jinja2Templates,
    request: Request,
    page: SkillsPage,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="skills.html",
        context=page.context.model_dump(),
        status_code=page.status_code,
    )
