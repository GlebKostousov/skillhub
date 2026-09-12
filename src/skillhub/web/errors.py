"""Преобразует ошибки в безопасные HTTP-ответы."""

from collections.abc import Mapping

import structlog
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from skillhub.core import SkillHubError

_FRAMEWORK_ERRORS = {
    400: ("bad_request", "Некорректный запрос."),
    404: ("not_found", "Запрошенные данные не найдены."),
    405: ("method_not_allowed", "Метод запроса не поддерживается."),
}


class QueryTooLongError(SkillHubError):
    """Описывает превышение публичного предела поисковой строки."""

    code = "query_too_long"
    status_code = 422
    public_message = "Поисковый запрос не должен превышать 128 символов."


class ReloadForbiddenError(SkillHubError):
    """Описывает отказ проверки источника или секретного токена перезагрузки."""

    code = "reload_forbidden"
    status_code = 403
    public_message = "Перезагрузка каталога запрещена."


class ReloadUnavailableError(SkillHubError):
    """Описывает временный отказ безопасного приёма перезагрузки."""

    code = "reload_unavailable"
    status_code = 409
    public_message = "Перезагрузка каталога сейчас недоступна. Повторите позже."


class RequestTooLargeError(SkillHubError):
    """Описывает превышение предела тела изменяющего запроса."""

    code = "request_too_large"
    status_code = 413
    public_message = "Тело запроса превышает допустимый размер."


class UnsupportedFormError(SkillHubError):
    """Описывает неподдерживаемый формат формы перезагрузки."""

    code = "unsupported_media_type"
    status_code = 415
    public_message = "Формат тела запроса не поддерживается."


def build_error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Формирует единую безопасную HTTP-оболочку."""
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={"error": {"code": code, "message": message}},
    )


def framework_error_response(
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Формирует стабильную безопасную оболочку системной ошибки.

    Args:
        status_code: исходный HTTP-статус.
        headers: безопасные транспортные заголовки.

    Returns:
        JSON-ответ с проектным кодом и русским сообщением.
    """
    code, message = _FRAMEWORK_ERRORS.get(
        status_code,
        ("http_error", "Запрос не выполнен."),
    )
    return build_error_response(status_code, code, message, headers=headers)


def skillhub_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Преобразует ожидаемую ошибку в безопасный ответ."""
    if not isinstance(exc, SkillHubError):
        raise TypeError
    structlog.get_logger(__name__).warning(
        "http.expected_error",
        error_code=exc.code,
        status_code=exc.status_code,
    )
    return build_error_response(exc.status_code, exc.code, exc.public_message)


def framework_http_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """Преобразует транспортную ошибку в безопасный ответ."""
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError
    code, _ = _FRAMEWORK_ERRORS.get(
        exc.status_code,
        ("http_error", "Запрос не выполнен."),
    )
    structlog.get_logger(__name__).warning(
        "http.framework_error",
        error_code=code,
        status_code=exc.status_code,
    )
    return framework_error_response(exc.status_code, headers=exc.headers)


def install_error_handlers(app: FastAPI) -> None:
    """Устанавливает обработчики ожидаемых и транспортных ошибок.

    Args:
        app: собираемое FastAPI-приложение.
    """
    app.add_exception_handler(SkillHubError, skillhub_error_handler)
    app.add_exception_handler(StarletteHTTPException, framework_http_error_handler)
