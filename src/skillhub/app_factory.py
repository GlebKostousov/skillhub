"""Собирает приложение и его инфраструктурные зависимости."""

import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from skillhub import web
from skillhub._server_logging import configure_server_logging
from skillhub.core import Settings, configure_logging
from skillhub.llm import DeepSeekLlmGateway, LlmGateway
from skillhub.registry import SkillRegistry
from skillhub.web import (
    ProcessErrorBoundary,
    SecurityHeadersMiddleware,
    StrictHostMiddleware,
    TrustedHostEnvelopeMiddleware,
    create_router,
    install_error_handlers,
)

_WEB_DIR = Path(__file__).parent / "web"
_DEFAULT_SKILLS_ROOT = Path("skills")
_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "testserver")


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
    gateway = llm_gateway if llm_gateway is not None else DeepSeekLlmGateway()
    app = FastAPI(title="SkillHub", debug=False)
    app.state.llm_gateway = gateway
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
    install_error_handlers(app)
    return app
