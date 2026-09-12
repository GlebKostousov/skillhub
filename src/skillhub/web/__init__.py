"""Собирает HTTP-маршруты и обработчики SkillHub."""

from skillhub.web._boundaries import (
    ProcessErrorBoundary,
    TrustedHostEnvelopeMiddleware,
)
from skillhub.web._host_guard import StrictHostMiddleware
from skillhub.web.assistant_routes import create_assistant_router
from skillhub.web.errors import install_error_handlers
from skillhub.web.protocol_routes import create_protocol_router
from skillhub.web.routes import create_router
from skillhub.web.security import SecurityHeadersMiddleware

__all__ = [
    "ProcessErrorBoundary",
    "SecurityHeadersMiddleware",
    "StrictHostMiddleware",
    "TrustedHostEnvelopeMiddleware",
    "create_assistant_router",
    "create_protocol_router",
    "create_router",
    "install_error_handlers",
]
