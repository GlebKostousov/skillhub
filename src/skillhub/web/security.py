"""Добавляет минимальную защиту локального веб-интерфейса."""

from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from skillhub.core import bind_request_id, clear_request_context

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def build_security_headers(request_id: str) -> dict[str, str]:
    """Собирает защитные заголовки для любого HTTP-ответа.

    Args:
        request_id: сгенерированный приложением идентификатор запроса.

    Returns:
        Заголовки защиты локального веб-интерфейса.
    """
    return {
        "Content-Security-Policy": CONTENT_SECURITY_POLICY,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Frame-Options": "DENY",
        "X-Request-ID": request_id,
    }


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Добавляет защитные заголовки и контекст идентификатора запроса."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Обрабатывает запрос в изолированном контексте логирования.

        Args:
            request: текущий HTTP-запрос.
            call_next: следующий обработчик middleware-цепочки.

        Returns:
            Ответ с защитными заголовками.
        """
        request_id = uuid4().hex
        request.state.request_id = request_id
        bind_request_id(request_id)
        try:
            response = await call_next(request)
            response.headers.update(build_security_headers(request_id))
            return response
        finally:
            clear_request_context()
