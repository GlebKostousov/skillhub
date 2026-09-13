"""Собирает приложение и его инфраструктурные зависимости."""

import secrets
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from skillhub import web
from skillhub._server_logging import configure_server_logging
from skillhub.assistant import ProtocolSkillHandler
from skillhub.core import Settings, configure_logging
from skillhub.llm import (
    DeepSeekLlmGateway,
    GenerationUnavailableError,
    LlmGateway,
    MeteredLlmGateway,
)
from skillhub.protocol import FinalizedProtocol, Protocol
from skillhub.registry import SkillRegistry
from skillhub.runtime import RuntimeStore
from skillhub.usage import UsageLedger, load_tariffs
from skillhub.web import (
    LoggingLlmGateway,
    ProcessErrorBoundary,
    SecurityHeadersMiddleware,
    StrictHostMiddleware,
    TrustedHostEnvelopeMiddleware,
    create_protocol_router,
    create_router,
    create_settings_router,
    create_usage_router,
    install_error_handlers,
)

_WEB_DIR = Path(__file__).parent / "web"
_DEFAULT_SKILLS_ROOT = Path("skills")
_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "testserver")
_TARIFFS_PATH = Path(__file__).resolve().parents[2] / "config" / "model-tariffs.yaml"


def create_app(
    *,
    skills_root: Path | None = None,
    llm_gateway: LlmGateway | None = None,
) -> FastAPI:
    """Собирает новый экземпляр локального приложения.

    Args:
        skills_root: доверенный корень скиллов или штатный каталог проекта.
        llm_gateway: явный шлюз модели или производственный адаптер DeepSeek.

    Returns:
        Настроенное приложение с одним загруженным реестром.
    """
    settings = Settings()
    configure_logging(settings.environment)
    configure_server_logging()
    registry = SkillRegistry(skills_root or _DEFAULT_SKILLS_ROOT)
    registry.reload()
    csrf_token = secrets.token_urlsafe(32)
    inner, gateway, ledger, store = _build_usage_gateway(llm_gateway, settings)
    app = FastAPI(title="SkillHub", debug=False)
    app.state.llm_gateway = gateway
    app.state.llm_transport = inner
    app.state.usage_ledger = ledger
    app.state.runtime_store = store
    app.add_middleware(
        TrustedHostEnvelopeMiddleware,
        allowed_hosts=_ALLOWED_HOSTS,
    )
    app.add_middleware(
        StrictHostMiddleware,
        allowed_hosts=_ALLOWED_HOSTS,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ProcessErrorBoundary)
    app.mount(
        "/static",
        StaticFiles(directory=_WEB_DIR / "static"),
        name="static",
    )
    templates = Jinja2Templates(directory=_WEB_DIR / "templates")
    app.include_router(
        create_router(
            templates,
            registry=registry,
            csrf_token=csrf_token,
        )
    )
    create_assistant_router = getattr(web, "create_assistant_router", None)
    if create_assistant_router is not None:
        app.include_router(
            create_assistant_router(
                templates,
                registry=registry,
                csrf_token=csrf_token,
                llm_gateway=gateway,
            )
        )
    app.include_router(
        create_protocol_router(
            templates,
            csrf_token=csrf_token,
            generator=None,
            reviser=_bind_protocol_reviser(registry, gateway),
        )
    )
    _attach_usage_routes(
        app,
        create_usage_router(
            templates,
            ledger=ledger,
            store=store,
        ),
    )
    _attach_usage_routes(
        app,
        create_settings_router(
            templates,
            csrf_token=csrf_token,
            store=store,
        ),
    )
    install_error_handlers(app)
    return app


def _bind_protocol_reviser(
    registry: SkillRegistry,
    gateway: LlmGateway,
) -> Callable[[Protocol, Sequence[Mapping[str, object]], str], FinalizedProtocol]:
    """Собирает повторную сборку протокола из снимка скилла и шлюза.

    Args:
        registry: файловый реестр скиллов текущего приложения.
        gateway: учётный шлюз модели.

    Returns:
        Вызов повторной сборки или отказ, если скилл протокола отсутствует.
    """
    handler = ProtocolSkillHandler(gateway)

    def reviser(
        protocol: Protocol,
        answers: Sequence[Mapping[str, object]],
        material: str,
    ) -> FinalizedProtocol:
        skill = registry.capture().snapshot.get("meeting-protocol")
        if skill is None:
            raise GenerationUnavailableError
        return handler.revise(skill, protocol, answers, material)

    return reviser


def _attach_usage_routes(app: FastAPI, router: APIRouter) -> None:
    """Подключает маршруты расходов без отдельного included-router.

    Args:
        app: собираемое приложение.
        router: маршрутизатор страницы и JSON расходов.
    """
    app.routes.extend(router.routes)


def _build_usage_gateway(
    llm_gateway: LlmGateway | None,
    settings: Settings,
) -> tuple[LlmGateway, LlmGateway, UsageLedger, RuntimeStore]:
    """Оборачивает шлюз учётом лимита и диагностическим логом.

    Args:
        llm_gateway: явный внутренний шлюз или производственный адаптер.
        settings: проверенная конфигурация журнала и дневного потолка.

    Returns:
        Внутренний шов, учётный шлюз, журнал расходов и хранилище снимка.
    """
    ledger = UsageLedger(settings.usage_path)
    catalog = load_tariffs(_TARIFFS_PATH)
    store = RuntimeStore(
        {item.model: item.max_tokens for item in catalog.models},
        daily_budget_nanos=settings.daily_budget_nanos,
    )
    inner = llm_gateway if llm_gateway is not None else DeepSeekLlmGateway(store=store)
    metered = MeteredLlmGateway(inner, ledger, catalog, store=store)
    return inner, LoggingLlmGateway(metered, ledger), ledger, store
