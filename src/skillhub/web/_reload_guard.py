"""Проверяет недоверенный запрос перезагрузки каталога."""

import re
import secrets
from urllib.parse import SplitResult, parse_qsl, urlsplit

from fastapi import Request

from skillhub.web.errors import (
    ReloadForbiddenError,
    RequestTooLargeError,
    UnsupportedFormError,
)

MAX_RELOAD_BODY_BYTES = 4_096
MAX_CONFIGURED_CSRF_TOKEN_LENGTH = 256
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_SUPPORTED_SCHEMES = {"http": 80, "https": 443}
_CONFIGURED_TOKEN_PATTERN = re.compile(
    rb"[A-Za-z0-9_-]{1," + str(MAX_CONFIGURED_CSRF_TOKEN_LENGTH).encode() + rb"}\Z"
)
_CSRF_CONFIGURATION_ERROR = (
    "CSRF-токен должен содержать от 1 до 256 безопасных для URL символов ASCII."
)
_MAX_PORT = 65_535
type _Origin = tuple[str, str, int]


def validate_csrf_token(token: str) -> None:
    """Проверяет настроенный секрет публичного маршрутизатора один раз.

    Args:
        token: секрет из безопасных для URL символов ASCII.

    Raises:
        ValueError: секрет не соответствует публичному контракту маршрутизатора.
    """
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(_CSRF_CONFIGURATION_ERROR) from exc
    if _CONFIGURED_TOKEN_PATTERN.fullmatch(encoded) is None:
        raise ValueError(_CSRF_CONFIGURATION_ERROR)


async def validate_reload_request(request: Request, expected_token: str) -> None:
    """Проверяет источник, размер формы и секретный токен перезагрузки.

    Args:
        request: недоверенный изменяющий HTTP-запрос.
        expected_token: случайный секрет текущего экземпляра приложения.

    Raises:
        ReloadForbiddenError: источник или токен не прошёл строгую проверку.
        RequestTooLargeError: заявленный или фактический размер превышен.
        UnsupportedFormError: форма имеет неподдерживаемый тип содержимого.
    """
    _require_same_origin(request)
    _require_form_content_type(request)
    submitted_token = _parse_form_token(await _read_bounded_body(request))
    if submitted_token is None or not _tokens_match(submitted_token, expected_token):
        raise ReloadForbiddenError


def _require_same_origin(request: Request) -> None:
    origin = _canonical_origin(_single_header(request, "origin"))
    host = _single_header(request, "host")
    expected = _canonical_origin(f"{request.url.scheme}://{host}") if host else None
    if origin is None or expected is None or origin != expected:
        raise ReloadForbiddenError


def _single_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else None


def _canonical_origin(value: str | None) -> _Origin | None:
    if not _is_plain_origin(value):
        return None
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return None
    return _validated_origin(value or "", parsed)


def _is_plain_origin(value: str | None) -> bool:
    return (
        bool(value)
        and value != "null"
        and not any(character.isspace() for character in value or "")
    )


def _validated_origin(value: str, parsed: SplitResult) -> _Origin | None:
    if (
        parsed.scheme not in _SUPPORTED_SCHEMES
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query != ""
        or parsed.fragment != ""
        or value != f"{parsed.scheme}://{parsed.netloc}"
    ):
        return None
    normalized_port = _normalized_port(parsed)
    if normalized_port is None:
        return None
    return parsed.scheme, parsed.hostname.casefold(), normalized_port


def _normalized_port(parsed: SplitResult) -> int | None:
    try:
        port = parsed.port
    except ValueError:
        return None
    normalized = _SUPPORTED_SCHEMES[parsed.scheme] if port is None else port
    return normalized if 1 <= normalized <= _MAX_PORT else None


def _require_form_content_type(request: Request) -> None:
    content_type = _single_header(request, "content-type")
    media_type = (
        content_type.partition(";")[0].strip().casefold() if content_type else ""
    )
    if media_type != _FORM_CONTENT_TYPE:
        raise UnsupportedFormError


async def _read_bounded_body(request: Request) -> bytes:
    _reject_oversized_declared_body(request)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_RELOAD_BODY_BYTES:
            raise RequestTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _reject_oversized_declared_body(request: Request) -> None:
    raw_length = _single_header(request, "content-length")
    if raw_length is None:
        return
    declared_length = _parse_declared_length(raw_length)
    if declared_length < 0 or declared_length > MAX_RELOAD_BODY_BYTES:
        raise RequestTooLargeError


def _parse_declared_length(raw_length: str) -> int:
    try:
        return int(raw_length)
    except ValueError as exc:
        raise RequestTooLargeError from exc


def _parse_form_token(body: bytes) -> str | None:
    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError):
        return None
    if len(pairs) != 1 or pairs[0][0] != "csrf_token":
        return None
    return pairs[0][1]


def _tokens_match(submitted_token: str, expected_token: str) -> bool:
    try:
        submitted = submitted_token.encode("ascii")
        expected = expected_token.encode("ascii")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(submitted, expected)
