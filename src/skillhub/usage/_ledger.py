"""Хранит committed-строки расходов в SQLite."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from skillhub.usage._errors import (
    DailyBudgetExceededError,
    LedgerError,
    SchemaVersionError,
)
from skillhub.usage._models import UsageEvent

SCHEMA_VERSION: Final[int] = 1
STALE_RESERVED_SECONDS: Final[int] = 30
RECENT_LIMIT: Final[int] = 50

_V1_TABLES: Final[frozenset[str]] = frozenset({"schema_version", "usage_events"})
_V1_VERSION_COLUMNS: Final[frozenset[str]] = frozenset({"version"})
_V1_EVENT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "request_id",
        "operation",
        "skill",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_hit",
        "cache_miss",
        "reasoning",
        "cost_nanos",
        "currency",
        "tariff_cache_hit",
        "tariff_cache_miss",
        "tariff_output",
        "tariff_source",
        "tariff_verified_at",
        "status",
        "created_at",
    }
)

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
_CREATE_STATUS_CREATED_INDEX = """
CREATE INDEX IF NOT EXISTS usage_events_status_created_at
ON usage_events (status, created_at)
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
WHERE status = ?
ORDER BY id
"""
_SELECT_BY_REQUEST = """
SELECT
    request_id, operation, skill, model,
    prompt_tokens, completion_tokens, total_tokens,
    cache_hit, cache_miss, reasoning,
    cost_nanos, currency,
    tariff_cache_hit, tariff_cache_miss, tariff_output,
    tariff_source, tariff_verified_at,
    status, created_at
FROM usage_events
WHERE status = ? AND request_id = ?
ORDER BY id
"""
_SELECT_RECENT = """
SELECT
    request_id, operation, skill, model,
    prompt_tokens, completion_tokens, total_tokens,
    cache_hit, cache_miss, reasoning,
    cost_nanos, currency,
    tariff_cache_hit, tariff_cache_miss, tariff_output,
    tariff_source, tariff_verified_at,
    status, created_at
FROM usage_events
WHERE status = ?
ORDER BY id DESC
LIMIT ?
"""
_SUM_STATUS_DAY = """
SELECT COALESCE(SUM(cost_nanos), 0)
FROM usage_events
WHERE status = ?
AND created_at >= ? AND created_at < ?
"""
_SUM_HELD = """
SELECT COALESCE(SUM(cost_nanos), 0)
FROM usage_events
WHERE status IN ('reserved', 'committed')
AND created_at >= ? AND created_at < ?
"""
_SUM_HELD_EXCEPT = """
SELECT COALESCE(SUM(cost_nanos), 0)
FROM usage_events
WHERE status IN ('reserved', 'committed')
AND created_at >= ? AND created_at < ?
AND id != ?
"""
_SELECT_RESERVED_CREATED = """
SELECT created_at FROM usage_events WHERE id = ? AND status = ?
"""
_UPDATE_COMMIT = """
UPDATE usage_events SET
    prompt_tokens = ?, completion_tokens = ?, total_tokens = ?,
    cache_hit = ?, cache_miss = ?, reasoning = ?,
    cost_nanos = ?,
    tariff_cache_hit = ?, tariff_cache_miss = ?, tariff_output = ?,
    status = ?
WHERE id = ? AND status = ?
"""
_UPDATE_RELEASE = """
UPDATE usage_events SET status = ? WHERE id = ? AND status = ?
"""
_RELEASE_STALE = """
UPDATE usage_events
SET status = ?
WHERE status = ? AND created_at < ?
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
        _reject_negative_event(event)
        with _connect(self._path) as connection:
            connection.execute(_INSERT_EVENT, _values(event))

    def reserve(self, event: UsageEvent, daily_budget_nanos: int) -> int:
        """Пишет reserved-строку, если дневной лимит ещё позволяет вызов.

        Args:
            event: worst-case оценка без пользовательского текста.
            daily_budget_nanos: дневной потолок в нано-USD.

        Returns:
            Идентификатор зарезервированной строки.

        Raises:
            DailyBudgetExceededError: резерв не помещается в оставшийся лимит.
        """
        _reject_negative_event(event)
        with _immediate(self._path) as connection:
            return _insert_reserved(connection, event, daily_budget_nanos)

    def commit(
        self,
        reservation_id: int,
        event: UsageEvent,
        daily_budget_nanos: int | None = None,
    ) -> None:
        """Заменяет reserved-строку фактическими токенами и ценой.

        Args:
            reservation_id: идентификатор ранее записанного резерва.
            event: фактическая committed-строка без пользовательского текста.
            daily_budget_nanos: дневной потолок в нано-USD или его отсутствие.
        """
        _reject_negative_event(event)
        with _connect(self._path) as connection:
            _apply_commit(connection, reservation_id, event, daily_budget_nanos)

    def release(self, reservation_id: int) -> None:
        """Возвращает зарезервированный лимит после отказа поставщика.

        Args:
            reservation_id: идентификатор ранее записанного резерва.
        """
        with _connect(self._path) as connection:
            _apply_release(connection, reservation_id)

    def list(self) -> tuple[UsageEvent, ...]:
        """Возвращает committed-строки в порядке записи.

        Returns:
            Неизменяемая последовательность событий этого файла.
        """
        with _connect(self._path) as connection:
            rows = connection.execute(_SELECT_EVENTS, ("committed",)).fetchall()
        return tuple(_event_from(row) for row in rows)

    def lookup(self, request_id: str) -> tuple[UsageEvent, ...]:
        """Возвращает committed-строки указанного запроса.

        Args:
            request_id: идентификатор запроса из журнала.

        Returns:
            События этого запроса в порядке записи.
        """
        with _connect(self._path) as connection:
            rows = connection.execute(
                _SELECT_BY_REQUEST,
                ("committed", request_id),
            ).fetchall()
        return tuple(_event_from(row) for row in rows)

    def recent(self, limit: int = RECENT_LIMIT) -> tuple[UsageEvent, ...]:
        """Возвращает последние committed-строки по убыванию идентификатора.

        Args:
            limit: наибольшее число строк в выборке.

        Returns:
            События от новых к старым.
        """
        with _connect(self._path) as connection:
            rows = connection.execute(
                _SELECT_RECENT,
                ("committed", limit),
            ).fetchall()
        return tuple(_event_from(row) for row in rows)

    def day_totals(self, day: date) -> tuple[int, int]:
        """Возвращает суммы committed и reserved за указанную UTC-дату.

        Args:
            day: календарная дата UTC.

        Returns:
            Пара (committed_nanos, reserved_nanos).
        """
        start, end = _date_bounds(day)
        with _connect(self._path) as connection:
            return (
                _sum_status(connection, "committed", start, end),
                _sum_status(connection, "reserved", start, end),
            )


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
        _ready_file(connection)


def _align_schema(connection: sqlite3.Connection) -> None:
    try:
        _align_or_create(connection)
    except sqlite3.Error:
        raise SchemaVersionError from None


def _align_or_create(connection: sqlite3.Connection) -> None:
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
    _reject_unexpected_layout(connection)


def _reject_unexpected_layout(connection: sqlite3.Connection) -> None:
    if _user_tables(connection) != _V1_TABLES:
        raise SchemaVersionError
    if _table_columns(connection, "schema_version") != _V1_VERSION_COLUMNS:
        raise SchemaVersionError
    if _table_columns(connection, "usage_events") != _V1_EVENT_COLUMNS:
        raise SchemaVersionError


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = ?",
        ("table",),
    ).fetchall()
    return {name for (name,) in rows if not str(name).startswith("sqlite_")}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if table == "schema_version":
        rows = connection.execute("PRAGMA table_info(schema_version)").fetchall()
        return {_as_str(row[1]) for row in rows}
    rows = connection.execute("PRAGMA table_info(usage_events)").fetchall()
    return {_as_str(row[1]) for row in rows}


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


def _ready_file(connection: sqlite3.Connection) -> None:
    try:
        connection.execute(_CREATE_STATUS_CREATED_INDEX)
        _release_stale_reserved(connection)
    except sqlite3.Error:
        raise LedgerError from None


def _release_stale_reserved(connection: sqlite3.Connection) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_RESERVED_SECONDS)
    connection.execute(_RELEASE_STALE, ("released", "reserved", cutoff.isoformat()))


def _insert_reserved(
    connection: sqlite3.Connection,
    event: UsageEvent,
    daily_budget_nanos: int,
) -> int:
    _reject_over_budget(connection, event, daily_budget_nanos)
    cursor = connection.execute(_INSERT_EVENT, _values(event))
    return _row_id(cursor)


def _reject_over_budget(
    connection: sqlite3.Connection,
    event: UsageEvent,
    daily_budget_nanos: int,
) -> None:
    held = _held_nanos(connection, event.created_at)
    if held + event.cost_nanos > daily_budget_nanos:
        raise DailyBudgetExceededError


def _held_nanos(connection: sqlite3.Connection, at: datetime) -> int:
    start, end = _utc_day_bounds(at)
    row = connection.execute(_SUM_HELD, (start, end)).fetchone()
    if row is None:
        return 0
    return _as_int(row[0])


def _sum_status(
    connection: sqlite3.Connection,
    status: str,
    start: str,
    end: str,
) -> int:
    row = connection.execute(_SUM_STATUS_DAY, (status, start, end)).fetchone()
    if row is None:
        return 0
    return _as_int(row[0])


def _utc_day_bounds(at: datetime) -> tuple[str, str]:
    return _date_bounds(date.fromisoformat(_utc_day(at)))


def _date_bounds(day: date) -> tuple[str, str]:
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def _utc_day(at: datetime) -> str:
    if at.tzinfo is None:
        return at.date().isoformat()
    return at.astimezone(UTC).date().isoformat()


def _row_id(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        raise LedgerError
    return row_id


def _apply_commit(
    connection: sqlite3.Connection,
    reservation_id: int,
    event: UsageEvent,
    daily_budget_nanos: int | None,
) -> None:
    cost = _commit_cost(connection, reservation_id, event, daily_budget_nanos)
    cursor = connection.execute(
        _UPDATE_COMMIT,
        _commit_values(event, cost, reservation_id),
    )
    _require_updated(cursor)


def _commit_cost(
    connection: sqlite3.Connection,
    reservation_id: int,
    event: UsageEvent,
    daily_budget_nanos: int | None,
) -> int:
    if daily_budget_nanos is None:
        return event.cost_nanos
    room = _room_for_row(connection, reservation_id, daily_budget_nanos)
    return min(event.cost_nanos, room)


def _room_for_row(
    connection: sqlite3.Connection,
    reservation_id: int,
    daily_budget_nanos: int,
) -> int:
    created_at = _reserved_created_at(connection, reservation_id)
    start, end = _utc_day_bounds(datetime.fromisoformat(created_at))
    others = _held_except(connection, start, end, reservation_id)
    return max(0, daily_budget_nanos - others)


def _held_except(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    reservation_id: int,
) -> int:
    row = connection.execute(
        _SUM_HELD_EXCEPT,
        (start, end, reservation_id),
    ).fetchone()
    if row is None:
        return 0
    return _as_int(row[0])


def _reserved_created_at(connection: sqlite3.Connection, reservation_id: int) -> str:
    row = connection.execute(
        _SELECT_RESERVED_CREATED,
        (reservation_id, "reserved"),
    ).fetchone()
    if row is None:
        raise LedgerError
    return _as_str(row[0])


def _apply_release(connection: sqlite3.Connection, reservation_id: int) -> None:
    cursor = connection.execute(
        _UPDATE_RELEASE,
        ("released", reservation_id, "reserved"),
    )
    _require_updated(cursor)


def _commit_values(
    event: UsageEvent,
    cost_nanos: int,
    reservation_id: int,
) -> tuple[object, ...]:
    return (
        event.prompt_tokens,
        event.completion_tokens,
        event.total_tokens,
        event.cache_hit,
        event.cache_miss,
        event.reasoning,
        cost_nanos,
        str(event.tariff_cache_hit),
        str(event.tariff_cache_miss),
        str(event.tariff_output),
        "committed",
        reservation_id,
        "reserved",
    )


def _require_updated(cursor: sqlite3.Cursor) -> None:
    if cursor.rowcount != 1:
        raise LedgerError


def _reject_negative_event(event: UsageEvent) -> None:
    tokens = (
        event.prompt_tokens,
        event.completion_tokens,
        event.total_tokens,
        event.cache_hit,
        event.cache_miss,
        event.cost_nanos,
    )
    if any(item < 0 for item in tokens):
        raise LedgerError
    if event.reasoning is not None and event.reasoning < 0:
        raise LedgerError


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


@contextmanager
def _immediate(path: Path) -> Iterator[sqlite3.Connection]:
    connection = _open_autocommit(path)
    try:
        with _write_lock(connection):
            yield connection
    finally:
        connection.close()


def _open_autocommit(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path, timeout=30.0)
    except sqlite3.Error:
        raise LedgerError from None
    connection.isolation_level = None
    return connection


@contextmanager
def _write_lock(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


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
