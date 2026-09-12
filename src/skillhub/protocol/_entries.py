"""Собирает строки раздела до следующего заголовка."""

from skillhub.protocol._constants import EMPTY_ITEM
from skillhub.protocol._cursor import LineCursor

type SourceLine = tuple[int, str]


def collect_until_heading(cursor: LineCursor) -> tuple[SourceLine, ...]:
    """Возвращает непустые строки раздела до следующего заголовка.

    Args:
        cursor: курсор исходных строк.

    Returns:
        Пары номера строки и содержимого.
    """
    collected: list[SourceLine] = []
    while not cursor.at_end() and not is_heading(cursor.peek()):
        _append_content(cursor, collected)
    return tuple(collected)


def is_heading(line: str) -> bool:
    """Проверяет, что строка начинает заголовок Markdown."""
    return line.startswith("#")


def is_placeholder_section(entries: tuple[SourceLine, ...]) -> bool:
    """Проверяет единственный пункт-заполнитель пустого раздела.

    Args:
        entries: собранные строки раздела.

    Returns:
        Признак нормализуемого пустого раздела.
    """
    return len(entries) == 1 and entries[0][1] == EMPTY_ITEM


def _append_content(cursor: LineCursor, collected: list[SourceLine]) -> None:
    line_no = cursor.line_number
    line = cursor.take()
    if line != "":
        collected.append((line_no, line))
