"""Воспроизводит нормативный Markdown из модели протокола."""

from skillhub.protocol._constants import (
    DATE_PREFIX,
    EMPTY_ITEM,
    H1_PREFIX,
    PARTICIPANTS_PREFIX,
    PLACEHOLDER,
    SECTION_TITLES,
    TASK_HEADER,
    TASK_SEPARATOR,
)
from skillhub.protocol._models import Protocol, ProtocolTask

_CELL_DELIMITER = "|"
_CELL_REPLACEMENT = "/"


def render(protocol: Protocol) -> str:
    """Возвращает нормативный Markdown для переданного протокола.

    Args:
        protocol: собранная модель протокола.

    Returns:
        Текст, который снова разбирается в ту же модель.
    """
    blocks = (
        _header_block(protocol),
        _bullet_block(SECTION_TITLES[0], protocol.discussion),
        _bullet_block(SECTION_TITLES[1], protocol.decisions),
        _task_block(protocol.tasks),
        _bullet_block(SECTION_TITLES[3], protocol.open_questions),
    )
    return "\n\n".join(blocks) + "\n"


def _header_block(protocol: Protocol) -> str:
    participants = _join_participants(protocol.participants)
    return "\n".join(
        (
            f"# {H1_PREFIX} {protocol.title}",
            "",
            f"{DATE_PREFIX} {protocol.date}",
            f"{PARTICIPANTS_PREFIX} {participants}",
        )
    )


def _join_participants(participants: tuple[str, ...]) -> str:
    if not participants:
        return PLACEHOLDER
    return ", ".join(participants)


def _bullet_block(title: str, items: tuple[str, ...]) -> str:
    return "\n".join((f"## {title}", *_bullet_lines(items)))


def _bullet_lines(items: tuple[str, ...]) -> tuple[str, ...]:
    if not items:
        return (EMPTY_ITEM,)
    return tuple(f"- {item}" for item in items)


def _task_block(tasks: tuple[ProtocolTask, ...]) -> str:
    return "\n".join((f"## {SECTION_TITLES[2]}", *_task_lines(tasks)))


def _task_lines(tasks: tuple[ProtocolTask, ...]) -> tuple[str, ...]:
    if not tasks:
        return (EMPTY_ITEM,)
    rows = tuple(
        (
            f"| {_task_cell(task.title)} | {_task_cell(task.assignee)} | "
            f"{_task_cell(task.due)} |"
        )
        for task in tasks
    )
    return (TASK_HEADER, TASK_SEPARATOR, *rows)


def _task_cell(value: str) -> str:
    return value.replace(_CELL_DELIMITER, _CELL_REPLACEMENT)
