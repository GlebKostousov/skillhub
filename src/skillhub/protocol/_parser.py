"""Разбирает принятый Markdown в нормативную модель протокола."""

from skillhub.protocol._constants import MAX_PROTOCOL_CHARS
from skillhub.protocol._cursor import LineCursor
from skillhub.protocol._errors import reject
from skillhub.protocol._header import parse_metadata, parse_title
from skillhub.protocol._models import Protocol
from skillhub.protocol._sections import parse_sections


def parse(text: str) -> Protocol:
    """Возвращает нормативную модель принятого текста протокола.

    Args:
        text: исходный Markdown протокола.

    Returns:
        Собранный протокол без молчаливого ремонта структуры.
    """
    _reject_oversized(text)
    cursor = LineCursor(tuple(text.splitlines()))
    title = parse_title(cursor)
    date, participants = parse_metadata(cursor)
    discussion, decisions, tasks, questions = parse_sections(cursor)
    _reject_trailing(cursor)
    return Protocol(
        title=title,
        date=date,
        participants=participants,
        discussion=discussion,
        decisions=decisions,
        tasks=tasks,
        open_questions=questions,
    )


def _reject_oversized(text: str) -> None:
    if len(text) > MAX_PROTOCOL_CHARS:
        reject(1, f"не более {MAX_PROTOCOL_CHARS} символов", str(len(text)))


def _reject_trailing(cursor: LineCursor) -> None:
    cursor.skip_blank()
    if not cursor.at_end():
        reject(cursor.line_number, "конец протокола", cursor.peek())
