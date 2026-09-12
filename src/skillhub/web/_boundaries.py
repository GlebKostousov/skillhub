"""Содержит внешние ASGI-границы Host и неожиданных ошибок."""

from collections.abc import Sequence
from uuid import uuid4

import structlog
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from skillhub.core import bind_request_id, clear_request_context
from skillhub.web.errors import (
    build_error_response,
    framework_error_response,
)
from skillhub.web.security import build_security_headers

_BAD_REQUEST_STATUS = 400
_ABORTED_RESPONSE_MESSAGE = "Передача HTTP-ответа прервана."


def _log_http_failure(
    event: str,
    *,
    error_type: str,
    error_code: str,
    request_id: str,
) -> None:
    bind_request_id(request_id)
    try:
        structlog.get_logger(__name__).error(
            event,
            error_type=error_type,
            error_code=error_code,
            status_code=500,
            request_id=request_id,
        )
    finally:
        clear_request_context()


def _unexpected_error_response(exc: Exception, request_id: str) -> JSONResponse:
    """Готовит единое безопасное событие и 500-ответ."""
    _log_http_failure(
        "http.unexpected_error",
        error_type=type(exc).__name__,
        error_code="internal_error",
        request_id=request_id,
    )
    return build_error_response(
        500,
        "internal_error",
        "Внутренняя ошибка сервера.",
        headers=build_security_headers(request_id),
    )


class TrustedHostEnvelopeMiddleware:
    """TrustedHost с безопасной JSON-оболочкой для отказа."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str]) -> None:
        """Создаёт проверку разрешённых имён хоста.

        Args:
            app: внутреннее ASGI-приложение.
            allowed_hosts: разрешённые локальные имена.
        """
        self._trusted_host = TrustedHostMiddleware(
            app,
            allowed_hosts=allowed_hosts,
            www_redirect=False,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Нормализует текстовый отказ TrustedHost."""
        replacing_response = False

        async def send_safe(message: Message) -> None:
            nonlocal replacing_response
            if message["type"] == "http.response.start":
                content_type = next(
                    (
                        value
                        for key, value in message["headers"]
                        if key == b"content-type"
                    ),
                    b"",
                )
                replacing_response = message[
                    "status"
                ] == _BAD_REQUEST_STATUS and content_type.startswith(b"text/plain")
                if replacing_response:
                    structlog.get_logger(__name__).warning(
                        "http.framework_error",
                        error_code="bad_request",
                        status_code=_BAD_REQUEST_STATUS,
                    )
                    response = framework_error_response(_BAD_REQUEST_STATUS)
                    await response(scope, receive, send)
                    return
            if not replacing_response:
                await send(message)

        await self._trusted_host(scope, receive, send_safe)


class ProcessErrorBoundary:
    """Завершает неожиданные HTTP-ошибки до серверного журнала ошибок."""

    def __init__(self, app: ASGIApp) -> None:
        """Сохраняет внутреннее ASGI-приложение.

        Args:
            app: приложение с обработчиками ожидаемых HTTP-ошибок.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Отправляет единый резервный ответ до начала передачи."""
        response_started = False

        async def send_tracked(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive, send_tracked)
        except Exception as exc:
            if scope["type"] != "http":
                raise
            state = scope.get("state")
            request_id = state.get("request_id") if isinstance(state, dict) else None
            safe_request_id = request_id if isinstance(request_id, str) else uuid4().hex
            if not response_started:
                response = _unexpected_error_response(exc, safe_request_id)
                await response(scope, receive, send)
                return
            _log_http_failure(
                "http.response_aborted",
                error_type="RuntimeError",
                error_code="response_aborted",
                request_id=safe_request_id,
            )
        else:
            return
        raise RuntimeError(_ABORTED_RESPONSE_MESSAGE) from None
