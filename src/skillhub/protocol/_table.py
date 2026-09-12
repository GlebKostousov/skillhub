"""Разбирает нормативную таблицу задач протокола."""

from skillhub.protocol._constants import (
    EMPTY_ITEM,
    TASK_COLUMN_COUNT,
    TASK_HEADER,
    TASK_SEPARATOR,
)
from skillhub.protocol._entries import SourceLine, is_placeholder_section
from skillhub.protocol._errors import reject
from skillhub.protocol._models import ProtocolTask

_HEADER_AND_SEPARATOR = 2
_MIN_TABLE_LINES = _HEADER_AND_SEPARATOR + 1


def parse_tasks(
    entries: tuple[SourceLine, ...],
    start_line: int,
) -> tuple[ProtocolTask, ...]:
    """Возвращает задачи раздела или пустой кортеж для заполнителя.

    Args:
        entries: непустые строки раздела с номерами.
        start_line: номер строки сразу после заголовка раздела.

    Returns:
        Задачи протокола без вычисления срока.
    """
    _reject_empty_section(entries, start_line)
    if is_placeholder_section(entries):
        return ()
    return _parse_table(entries)


def _reject_empty_section(entries: tuple[SourceLine, ...], start_line: int) -> None:
    if not entries:
        reject(start_line, EMPTY_ITEM, "")


def _parse_table(entries: tuple[SourceLine, ...]) -> tuple[ProtocolTask, ...]:
    _require_line(entries[0], TASK_HEADER)
    _require_separator_and_rows(entries)
    return tuple(_parse_row(entry) for entry in entries[2:])


def _require_separator_and_rows(entries: tuple[SourceLine, ...]) -> None:
    if len(entries) < _HEADER_AND_SEPARATOR:
        reject(entries[0][0] + 1, TASK_SEPARATOR, "")
    _require_line(entries[1], TASK_SEPARATOR)
    if len(entries) < _MIN_TABLE_LINES:
        reject(entries[1][0] + 1, "строка задачи", "")


def _require_line(entry: SourceLine, expected: str) -> None:
    line_no, line = entry
    if line != expected:
        reject(line_no, expected, line)


def _parse_row(entry: SourceLine) -> ProtocolTask:
    line_no, line = entry
    title, assignee, due = _three_cells(line_no, line)
    return ProtocolTask(title=title, assignee=assignee, due=due)


def _three_cells(line_no: int, line: str) -> tuple[str, str, str]:
    raw = _split_row(line_no, line)
    if len(raw) != TASK_COLUMN_COUNT:
        reject(line_no, "три колонки", line)
    cells = tuple(cell.strip() for cell in raw)
    _reject_empty_cells(line_no, line, cells)
    return cells[0], cells[1], cells[2]


def _split_row(line_no: int, line: str) -> list[str]:
    if not line.startswith("|") or not line.endswith("|"):
        reject(line_no, "три колонки", line)
    return line[1:-1].split("|")


def _reject_empty_cells(line_no: int, line: str, cells: tuple[str, ...]) -> None:
    if any(not cell for cell in cells):
        reject(line_no, "значение или —", line)
