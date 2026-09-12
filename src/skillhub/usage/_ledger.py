"""Хранит committed-строки расходов в SQLite."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from skillhub.usage._errors import LedgerError, SchemaVersionError
from skillhub.usage._models import UsageEvent

SCHEMA_VERSION: Final[int] = 1

_CREATE_VERSION = """
CREATE TABLE schema_version (
    version INTEGER NOT NULL
)
"""
_CREATE_EVENTS = """
CREATE TABLE usage_events (
    id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    skill TEXT,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cache_hit INTEGER NOT NULL,
    cache_miss INTEGER NOT NULL,
    reasoning INTEGER,
    cost_nanos INTEGER NOT NULL,
    currency TEXT NOT NULL,
    tariff_cache_hit TEXT NOT NULL,
    tariff_cache_miss TEXT NOT NULL,
    tariff_output TEXT NOT NULL,
    tariff_source TEXT NOT NULL,
    tariff_verified_at TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_INSERT_EVENT = """
INSERT INTO usage_events (
    request_id, operation, skill, model,
    prompt_tokens, completion_tokens, total_tokens,
    cache_hit, cache_miss, reasoning,
    cost_nanos, currency,
    tariff_cache_hit, tariff_cache_miss, tariff_output,
    tariff_source, tariff_verified_at,
    status, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_EVENTS = """
SELECT
    request_id, operation, skill, model,
    prompt_tokens, completion_tokens, total_tokens,
    cache_hit, cache_miss, reasoning,
    cost_nanos, currency,
    tariff_cache_hit, tariff_cache_miss, tariff_output,
    tariff_source, tariff_verified_at,
    status, created_at
FROM usage_events
ORDER BY id
"""


class UsageLedger:
    """Журнал расходов в одном файле SQLite."""

    def __init__(self, path: Path) -> None:
        """Открывает существующий файл v1 или создаёт новый.

        Args:
            path: путь к файлу журнала, заданный вызывающим кодом.
        """
        self._path = path
        _prepare(path)

    def record(self, event: UsageEvent) -> None:
        """Добавляет одну строку журнала.

        Args:
            event: типизированная запись без пользовательского текста.
        """
        with _connect(self._path) as connection:
            connection.execute(_INSERT_EVENT, _values(event))

    def list(self) -> tuple[UsageEvent, ...]:
        """Возвращает committed-строки в порядке записи.

        Returns:
            Неизменяемая последовательность событий этого файла.
        """
        with _connect(self._path) as connection:
            rows = connection.execute(_SELECT_EVENTS).fetchall()
        return tuple(_event_from(row) for row in rows)


def open_ledger(path: Path) -> UsageLedger:
    """Открывает журнал расходов по переданному пути.

    Args:
        path: путь к файлу SQLite.

    Returns:
        Журнал, готовый к записи и чтению.
    """
    return UsageLedger(path)


def _prepare(path: Path) -> None:
    with _connect(path) as connection:
        _align_schema(connection)


def _align_schema(connection: sqlite3.Connection) -> None:
    if _has_tables(connection):
        _reject_foreign(connection)
        return
    _create_v1(connection)


def _has_tables(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = ?",
        ("table",),
    ).fetchall()
    return bool(rows)


def _reject_foreign(connection: sqlite3.Connection) -> None:
    if _read_version(connection) != SCHEMA_VERSION:
        raise SchemaVersionError


def _read_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.Error:
        raise SchemaVersionError from None
    return _version_value(row)


def _version_value(row: tuple[object, ...] | None) -> int:
    if row is None:
        raise SchemaVersionError
    return _as_int(row[0])


def _create_v1(connection: sqlite3.Connection) -> None:
    connection.execute(_CREATE_VERSION)
    connection.execute(
        "INSERT INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    connection.execute(_CREATE_EVENTS)


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        raise LedgerError from None
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _values(event: UsageEvent) -> tuple[object, ...]:
    return (
        event.request_id,
        event.operation,
        event.skill,
        event.model,
        event.prompt_tokens,
        event.completion_tokens,
        event.total_tokens,
        event.cache_hit,
        event.cache_miss,
        event.reasoning,
        event.cost_nanos,
        event.currency,
        str(event.tariff_cache_hit),
        str(event.tariff_cache_miss),
        str(event.tariff_output),
        event.tariff_source,
        event.tariff_verified_at.isoformat(),
        event.status,
        event.created_at.isoformat(),
    )


def _event_from(row: tuple[object, ...]) -> UsageEvent:
    return UsageEvent(
        request_id=_as_str(row[0]),
        operation=_as_str(row[1]),
        skill=_optional_str(row[2]),
        model=_as_str(row[3]),
        prompt_tokens=_as_int(row[4]),
        completion_tokens=_as_int(row[5]),
        total_tokens=_as_int(row[6]),
        cache_hit=_as_int(row[7]),
        cache_miss=_as_int(row[8]),
        reasoning=_optional_int(row[9]),
        cost_nanos=_as_int(row[10]),
        currency=_as_str(row[11]),
        tariff_cache_hit=Decimal(_as_str(row[12])),
        tariff_cache_miss=Decimal(_as_str(row[13])),
        tariff_output=Decimal(_as_str(row[14])),
        tariff_source=_as_str(row[15]),
        tariff_verified_at=date.fromisoformat(_as_str(row[16])),
        status=_as_str(row[17]),
        created_at=datetime.fromisoformat(_as_str(row[18])),
    )


def _as_str(value: object) -> str:
    if type(value) is str:
        return value
    raise LedgerError


def _as_int(value: object) -> int:
    if type(value) is int:
        return value
    raise LedgerError


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return _as_str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _as_int(value)
