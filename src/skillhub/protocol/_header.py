"""Разбирает заголовок и метаданные нормативного протокола."""

from skillhub.protocol._constants import (
    DATE_PREFIX,
    H1_PREFIX,
    PARTICIPANTS_PREFIX,
    PLACEHOLDER,
)
from skillhub.protocol._cursor import LineCursor
from skillhub.protocol._errors import reject

_H1_START = f"# {H1_PREFIX} "


def parse_title(cursor: LineCursor) -> str:
    """Возвращает тему встречи из единственного заголовка первого уровня.

    Args:
        cursor: курсор исходных строк.

    Returns:
        Тема встречи без префикса формата.
    """
    cursor.skip_blank()
    line_no = cursor.line_number
    line = cursor.take()
    _require_prefix(line_no, line, _H1_START, f"# {H1_PREFIX}")
    return _nonempty_suffix(line_no, line, _H1_START, "тема встречи")


def parse_metadata(cursor: LineCursor) -> tuple[str, tuple[str, ...]]:
    """Возвращает дату и имена участников в нормативном порядке.

    Args:
        cursor: курсор исходных строк.

    Returns:
        Дата встречи и кортеж имён участников.
    """
    cursor.skip_blank()
    date = _prefixed_field(cursor, DATE_PREFIX)
    raw_participants, line_no = _field_with_line(cursor, PARTICIPANTS_PREFIX)
    return date, _participant_names(line_no, raw_participants)


def _prefixed_field(cursor: LineCursor, prefix: str) -> str:
    value, _line_no = _field_with_line(cursor, prefix)
    return value


def _field_with_line(cursor: LineCursor, prefix: str) -> tuple[str, int]:
    line_no = cursor.line_number
    line = cursor.take()
    start = f"{prefix} "
    _require_prefix(line_no, line, start, prefix)
    return _nonempty_suffix(line_no, line, start, prefix), line_no


def _require_prefix(line_no: int, line: str, start: str, expected: str) -> None:
    if not line.startswith(start):
        reject(line_no, expected, line)


def _nonempty_suffix(line_no: int, line: str, start: str, expected: str) -> str:
    value = line.removeprefix(start)
    if not value:
        reject(line_no, expected, line)
    return value


def _participant_names(line_no: int, raw: str) -> tuple[str, ...]:
    if raw == PLACEHOLDER:
        return ()
    return _split_names(line_no, raw)


def _split_names(line_no: int, raw: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in raw.split(","))
    _reject_empty_names(line_no, raw, names)
    return names


def _reject_empty_names(line_no: int, raw: str, names: tuple[str, ...]) -> None:
    if any(not name for name in names):
        reject(line_no, PARTICIPANTS_PREFIX, raw)
