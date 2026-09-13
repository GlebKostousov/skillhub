"""Разбирает маленькую структурированную команду модели."""

import json
import re
from typing import Any, cast

_FENCE = re.compile(
    r"^```(?:json)?\s*(.*?)\s*```$",
    re.DOTALL | re.IGNORECASE,
)


def parse_skill_name(text: str) -> str | None:
    """Извлекает имя режима из ответа модели.

    Args:
        text: сырой текст успешного ответа шлюза.

    Returns:
        Кандидат имени или отсутствие выбора при битом ответе.
    """
    for candidate in _payload_candidates(text):
        name = _name_from_payload(candidate)
        if name is not None:
            return name
    return None


def _payload_candidates(text: str) -> tuple[object, ...]:
    payloads: list[object] = []
    for raw in (text, _unwrap_fence(text), _first_object(text)):
        loaded = _load_json(raw)
        if loaded is not None:
            payloads.append(loaded)
    return tuple(payloads)


def _unwrap_fence(text: str) -> str:
    match = _FENCE.match(text.strip())
    if match is None:
        return text
    return match.group(1)


def _first_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]


def _load_json(text: str) -> object | None:
    try:
        loaded: object = cast("object", json.loads(text))
    except json.JSONDecodeError:
        return None
    return loaded


def _name_from_payload(payload: object) -> str | None:
    if type(payload) is not dict:
        return None
    mapping = cast("dict[str, Any]", payload)
    if "skill" not in mapping:
        return None
    return _name_from_value(mapping["skill"])


def _name_from_value(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned.replace("_", "-")
