"""Проверяет точное локальное значение Host до TrustedHost."""

import re
from collections.abc import Sequence
from typing import cast

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from skillhub.web.errors import framework_error_response

_BAD_REQUEST_STATUS = 400
_MAX_PORT = 65_535
_MAX_PORT_DIGITS = 5


class StrictHostMiddleware:
    """Отклоняет неоднозначный или недопустимый заголовок Host."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str]) -> None:
        """Создаёт точную проверку разрешённых локальных имён."""
        self._app = app
        alternatives = "|".join(re.escape(host) for host in allowed_hosts)
        self._authority_pattern = re.compile(
            rf"(?:{alternatives})(?::([0-9]+))?\Z",
            re.ASCII,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Передаёт только запрос с одним строгим значением Host."""
        if scope["type"] == "http" and not self._is_allowed(scope):
            structlog.get_logger(__name__).warning(
                "http.framework_error",
                error_code="bad_request",
                status_code=_BAD_REQUEST_STATUS,
            )
            await framework_error_response(_BAD_REQUEST_STATUS)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _is_allowed(self, scope: Scope) -> bool:
        authority = _single_ascii_host(scope)
        match = (
            self._authority_pattern.fullmatch(authority)
            if authority is not None
            else None
        )
        return match is not None and _valid_decimal_port(match.group(1))


def _single_ascii_host(scope: Scope) -> str | None:
    headers = cast("Sequence[tuple[bytes, bytes]]", scope.get("headers", ()))
    values = [value for name, value in headers if name.lower() == b"host"]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _valid_decimal_port(raw_port: str | None) -> bool:
    if raw_port is None:
        return True
    normalized = raw_port.lstrip("0")
    return (
        bool(normalized)
        and len(normalized) <= _MAX_PORT_DIGITS
        and int(normalized) <= _MAX_PORT
    )
