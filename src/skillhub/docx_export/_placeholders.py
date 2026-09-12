# ruff: noqa: RUF001
"""Подставляет заполнители Word для пустых полей протокола."""

_MODEL_PLACEHOLDER = "\u2014"
EMPTY_SECTION = "Раздел не заполнен"
UNASSIGNED = "Не назначен"
UNSPECIFIED_DUE = "Не определён"


def section_items(items: tuple[str, ...]) -> tuple[str, ...]:
    """Возвращает пункты раздела или единственный заполнитель пустоты.

    Args:
        items: пункты нормативной модели.

    Returns:
        Исходные пункты либо кортеж с заполнителем пустого раздела.
    """
    if not items:
        return (EMPTY_SECTION,)
    return items


def assignee_label(value: str) -> str:
    """Возвращает ответственного или заполнитель отсутствия назначения.

    Args:
        value: ответственный из модели задачи.

    Returns:
        Исходное имя либо заполнитель Word.
    """
    if _is_missing(value):
        return UNASSIGNED
    return value


def due_label(value: str) -> str:
    """Возвращает срок или заполнитель неопределённой даты.

    Args:
        value: срок из модели задачи.

    Returns:
        Исходный срок либо заполнитель Word.
    """
    if _is_missing(value):
        return UNSPECIFIED_DUE
    return value


def participants_label(participants: tuple[str, ...]) -> str:
    """Возвращает список участников или модельный заполнитель.

    Args:
        participants: известные имена участников.

    Returns:
        Перечисление имён либо модельный прочерк.
    """
    if not participants:
        return _MODEL_PLACEHOLDER
    return ", ".join(participants)


def _is_missing(value: str) -> bool:
    return value in {"", _MODEL_PLACEHOLDER}
