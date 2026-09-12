"""Разбирает четыре обязательных раздела протокола в фиксированном порядке."""

from collections.abc import Callable

from skillhub.protocol._constants import SECTION_TITLES
from skillhub.protocol._cursor import LineCursor
from skillhub.protocol._entries import SourceLine, collect_until_heading
from skillhub.protocol._errors import reject
from skillhub.protocol._lists import parse_bullets
from skillhub.protocol._models import ProtocolTask
from skillhub.protocol._table import parse_tasks

type SectionParser[T] = Callable[[tuple[SourceLine, ...], int], tuple[T, ...]]


def parse_sections(
    cursor: LineCursor,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[ProtocolTask, ...], tuple[str, ...]]:
    """Возвращает содержимое четырёх нормативных разделов.

    Args:
        cursor: курсор исходных строк.

    Returns:
        Обсуждение, решения, задачи и открытые вопросы.
    """
    discussion = _parsed_section(cursor, SECTION_TITLES[0], parse_bullets)
    decisions = _parsed_section(cursor, SECTION_TITLES[1], parse_bullets)
    tasks = _parsed_section(cursor, SECTION_TITLES[2], parse_tasks)
    questions = _parsed_section(cursor, SECTION_TITLES[3], parse_bullets)
    return discussion, decisions, tasks, questions


def _parsed_section[T](
    cursor: LineCursor,
    title: str,
    parser: SectionParser[T],
) -> tuple[T, ...]:
    start_line = _expect_heading(cursor, title)
    return parser(collect_until_heading(cursor), start_line)


def _expect_heading(cursor: LineCursor, title: str) -> int:
    cursor.skip_blank()
    line_no = cursor.line_number
    line = cursor.take()
    expected = f"## {title}"
    if line != expected:
        reject(line_no, expected, line)
    return cursor.line_number
