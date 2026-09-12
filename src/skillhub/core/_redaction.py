"""Ограничивает значения перед машинной сериализацией логов."""

import json
import math
from itertools import islice
from typing import cast

from structlog.typing import EventDict, WrappedLogger

MAX_FIELDS = 16
MAX_OUTPUT_BYTES = 8_192
_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_UNSUPPORTED = "[UNSUPPORTED]"
_MAX_TEXT_CHARS = 512
_DIAGNOSTIC_FIELDS = frozenset(
    {
        "event",
        "error_code",
        "status_code",
        "error_type",
        "request_id",
        "skill_path",
        "level",
        "timestamp",
    }
)
_MAX_DIAGNOSTIC_FIELD_LENGTH = max(map(len, _DIAGNOSTIC_FIELDS))
_MAX_INTEGER_BITS = 64
_FALLBACK_JSON = '{"event":"log.truncated","truncation":"[TRUNCATED]"}'


def _bounded_text(value: str) -> str:
    prefix = value[: _MAX_TEXT_CHARS + 1]
    if len(prefix) <= _MAX_TEXT_CHARS:
        return prefix
    kept_chars = _MAX_TEXT_CHARS - len(_TRUNCATED)
    return f"{prefix[:kept_chars]}{_TRUNCATED}"


def _is_diagnostic_field(field_name: str) -> bool:
    return (
        len(field_name) <= _MAX_DIAGNOSTIC_FIELD_LENGTH
        and field_name in _DIAGNOSTIC_FIELDS
    )


def _redact_scalar(value: object) -> object:
    value_type = type(value)
    if value_type is str:
        return _bounded_text(cast("str", value))
    if value is None or value_type is bool:
        return value
    if value_type is int:
        integer = cast("int", value)
        return integer if integer.bit_length() <= _MAX_INTEGER_BITS else _UNSUPPORTED
    if value_type is float:
        number = cast("float", value)
        return number if math.isfinite(number) else _UNSUPPORTED
    return _UNSUPPORTED


def redact_sensitive_fields(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Зачищает событие до фиксированного набора диагностических скаляров."""
    redacted: dict[str, object] = {}
    for key, value in islice(event_dict.items(), MAX_FIELDS):
        if type(key) is not str:
            redacted[_UNSUPPORTED] = _UNSUPPORTED
            continue
        field_name = key
        safe_key = _bounded_text(field_name)
        redacted[safe_key] = (
            _redact_scalar(value) if _is_diagnostic_field(field_name) else _REDACTED
        )
    if len(event_dict) > MAX_FIELDS:
        redacted[_TRUNCATED] = _TRUNCATED
    return cast("EventDict", redacted)


def render_bounded_json(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> str:
    """Сериализует ограниченное событие с жёстким пределом в байтах."""
    rendered = json.dumps(
        event_dict,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return rendered if len(rendered) + 1 <= MAX_OUTPUT_BYTES else _FALLBACK_JSON
