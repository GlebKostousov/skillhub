"""Проверяет недоверенный JSON-запрос окна ассистента."""

import json
import re
import secrets
from typing import cast
from urllib.parse import SplitResult, urlsplit

from fastapi import Request

from skillhub.core import SkillHubError
from skillhub.web.errors import RequestTooLargeError, UnsupportedFormError

MAX_ASSISTANT_BODY_BYTES = 1_048_576
MAX_CONFIGURED_CSRF_TOKEN_LENGTH = 256
_JSON_CONTENT_TYPE = "application/json"
_SUPPORTED_SCHEMES = {"http": 80, "https": 443}
_CONFIGURED_TOKEN_PATTERN = re.compile(
    rb"[A-Za-z0-9_-]{1," + str(MAX_CONFIGURED_CSRF_TOKEN_LENGTH).encode() + rb"}\Z"
)
_CSRF_CONFIGURATION_ERROR = (
    "CSRF-токен должен содержать от 1 до 256 безопасных для URL символов ASCII."
)
_MAX_PORT = 65_535
type _Origin = tuple[str, str, int]


class AssistantForbiddenError(SkillHubError):
    """Описывает отказ проверки источника или секретного токена ассистента."""

    code = "assistant_forbidden"
    status_code = 403
    public_message = "Запрос ассистента запрещён."


class AssistantRequestError(SkillHubError):
    """Описывает некорректное JSON-тело запроса ассистента."""

    code = "bad_request"
    status_code = 400
    public_message = "Некорректный запрос."


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


async def validate_assistant_request(
    request: Request,
    expected_token: str,
) -> tuple[str, str]:
    """Проверяет источник, размер JSON и секретный токен ассистента.

    Args:
        request: недоверенный изменяющий HTTP-запрос.
        expected_token: случайный секрет текущего экземпляра приложения.

    Returns:
        Намерение и материал из проверенного тела.

    Raises:
        AssistantForbiddenError: источник или токен не прошёл строгую проверку.
        AssistantRequestError: тело не является корректным JSON UTF-8.
        RequestTooLargeError: заявленный или фактический размер превышен.
        UnsupportedFormError: тело имеет неподдерживаемый тип содержимого.
    """
    _require_same_origin(request)
    _require_json_content_type(request)
    payload = _parse_json_payload(await _read_bounded_body(request))
    if not _csrf_matches(request, payload, expected_token):
        raise AssistantForbiddenError
    return (
        _require_text_field(payload, "intent"),
        _require_text_field(payload, "material"),
    )


def _require_same_origin(request: Request) -> None:
    origin = _canonical_origin(_single_header(request, "origin"))
    host = _single_header(request, "host")
    expected = _canonical_origin(f"{request.url.scheme}://{host}") if host else None
    if origin is None or expected is None or origin != expected:
        raise AssistantForbiddenError


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


def _require_json_content_type(request: Request) -> None:
    content_type = _single_header(request, "content-type")
    media_type = (
        content_type.partition(";")[0].strip().casefold() if content_type else ""
    )
    if media_type != _JSON_CONTENT_TYPE:
        raise UnsupportedFormError


async def _read_bounded_body(request: Request) -> bytes:
    _reject_oversized_declared_body(request)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ASSISTANT_BODY_BYTES:
            raise RequestTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _reject_oversized_declared_body(request: Request) -> None:
    raw_length = _single_header(request, "content-length")
    if raw_length is None:
        return
    declared_length = _parse_declared_length(raw_length)
    if declared_length < 0 or declared_length > MAX_ASSISTANT_BODY_BYTES:
        raise RequestTooLargeError


def _parse_declared_length(raw_length: str) -> int:
    try:
        return int(raw_length)
    except ValueError as exc:
        raise RequestTooLargeError from exc


def _parse_json_payload(body: bytes) -> dict[str, object]:
    try:
        payload: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssistantRequestError from exc
    if type(payload) is not dict:
        raise AssistantRequestError
    return cast("dict[str, object]", payload)


def _csrf_matches(
    request: Request,
    payload: dict[str, object],
    expected_token: str,
) -> bool:
    header_token = _single_header(request, "x-csrf-token")
    field_token = _optional_token_field(payload)
    candidates = [token for token in (header_token, field_token) if token is not None]
    if not candidates:
        return False
    return all(_tokens_match(token, expected_token) for token in candidates)


def _optional_token_field(payload: dict[str, object]) -> str | None:
    if "csrf_token" not in payload:
        return None
    value = payload["csrf_token"]
    return value if type(value) is str else None


def _require_text_field(payload: dict[str, object], name: str) -> str:
    if name not in payload:
        raise AssistantRequestError
    value = payload[name]
    if type(value) is not str:
        raise AssistantRequestError
    return value


def _tokens_match(submitted_token: str, expected_token: str) -> bool:
    try:
        submitted = submitted_token.encode("ascii")
        expected = expected_token.encode("ascii")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(submitted, expected)
