"""Разбирает маленькую структурированную команду модели."""

import json
from typing import Any, cast


def parse_skill_name(text: str) -> str | None:
    """Извлекает имя режима из ответа модели.

    Args:
        text: сырой текст успешного ответа шлюза.

    Returns:
        Кандидат имени или отсутствие выбора при битом ответе.
    """
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _name_from_payload(payload)


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
    return value
