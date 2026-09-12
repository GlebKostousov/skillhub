"""Разбирает маркированные пункты раздела протокола."""

from skillhub.protocol._constants import EMPTY_ITEM
from skillhub.protocol._entries import SourceLine, is_placeholder_section
from skillhub.protocol._errors import reject


def parse_bullets(entries: tuple[SourceLine, ...], start_line: int) -> tuple[str, ...]:
    """Возвращает пункты раздела или пустой кортеж для заполнителя.

    Args:
        entries: непустые строки раздела с номерами.
        start_line: номер строки сразу после заголовка раздела.

    Returns:
        Пункты раздела без маркера списка.
    """
    _reject_empty_section(entries, start_line)
    if is_placeholder_section(entries):
        return ()
    return tuple(_bullet_text(line_no, line) for line_no, line in entries)


def _reject_empty_section(entries: tuple[SourceLine, ...], start_line: int) -> None:
    if not entries:
        reject(start_line, EMPTY_ITEM, "")


def _bullet_text(line_no: int, line: str) -> str:
    if not line.startswith("- "):
        reject(line_no, "- пункт", line)
    text = line.removeprefix("- ")
    if not text:
        reject(line_no, "- пункт", line)
    return text
