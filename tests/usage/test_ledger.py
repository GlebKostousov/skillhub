"""Проверяет журнал расходов на рестарт и снимок тарифа."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from skillhub.usage import (
    LedgerError,
    SchemaVersionError,
    UsageEvent,
    open_ledger,
)
from skillhub.usage._ledger import STALE_RESERVED_SECONDS

_REQUEST_ID = "req-ledger-1"
_MODEL = "deepseek-flash"


def test_committed_event_survives_reopen(tmp_path: Path) -> None:
    """Проверяет, что повторное открытие файла читает те же committed-строки."""
    path = tmp_path / "usage.sqlite3"
    event = _committed_event(cost_nanos=300_000_000)
    open_ledger(path).record(event)

    restored = open_ledger(path).list()

    assert restored == (event,)


def test_recorded_cost_stays_after_new_event(tmp_path: Path) -> None:
    """Проверяет, что новая запись не пересчитывает уже сохранённую стоимость."""
    path = tmp_path / "usage.sqlite3"
    ledger = open_ledger(path)
    first = _committed_event(cost_nanos=300_000_000)
    second = _committed_event(
        request_id="req-ledger-2",
        cost_nanos=150_000_000,
        tariff_cache_miss=Decimal("0.15"),
    )
    ledger.record(first)
    ledger.record(second)

    stored = open_ledger(path).list()

    assert stored[0].cost_nanos == 300_000_000
    assert stored[1].cost_nanos == 150_000_000
    assert stored[0].tariff_cache_miss == Decimal("0.30")


def test_stale_reserved_is_released_on_reopen(tmp_path: Path) -> None:
    """Проверяет снятие просроченного reserved при повторном открытии."""
    path = tmp_path / "usage.sqlite3"
    stale = replace(
        _committed_event(cost_nanos=250),
        status="reserved",
        created_at=datetime.now(UTC) - timedelta(seconds=STALE_RESERVED_SECONDS + 1),
    )
    open_ledger(path).record(stale)

    restored = open_ledger(path)
    committed, reserved = restored.day_totals(stale.created_at.astimezone(UTC).date())

    assert reserved == 0
    assert committed == 0
    assert restored.list() == ()


def test_v1_without_events_table_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ чужого SQLite v1 без usage_events и сохранность таблиц."""
    path = tmp_path / "usage.sqlite3"
    _write_v1_without_events(path)

    with pytest.raises(SchemaVersionError) as captured:
        open_ledger(path)

    assert captured.value.code == "unsupported_schema"
    assert _fetch_keep_me(path) == (1,)
    assert "usage_events" not in _table_names(path)
    assert "keep_me" in _table_names(path)


def test_non_sqlite_file_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ файла, который не является базой SQLite."""
    path = tmp_path / "usage.sqlite3"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises((SchemaVersionError, LedgerError)):
        open_ledger(path)


def test_foreign_schema_version_is_rejected_without_rewrite(tmp_path: Path) -> None:
    """Проверяет отказ чужой schema_version без разрушающей миграции."""
    path = tmp_path / "usage.sqlite3"
    _write_foreign_schema(path)

    with pytest.raises(SchemaVersionError) as captured:
        open_ledger(path)

    assert captured.value.code == "unsupported_schema"
    kept = _fetch_keep_me(path)
    assert kept == (1,)


def test_schema_has_no_user_content_columns(tmp_path: Path) -> None:
    """Проверяет отсутствие пользовательских полей в схеме журнала."""
    path = tmp_path / "usage.sqlite3"
    open_ledger(path)

    names = _event_columns(path)

    assert {"intent", "material", "content", "text"}.isdisjoint(names)


def _committed_event(
    *,
    request_id: str = _REQUEST_ID,
    cost_nanos: int,
    tariff_cache_miss: Decimal = Decimal("0.30"),
) -> UsageEvent:
    """Собирает зафиксированную строку журнала с известной стоимостью."""
    return UsageEvent(
        request_id=request_id,
        operation="unspecified",
        skill=None,
        model=_MODEL,
        prompt_tokens=1_000_000,
        completion_tokens=0,
        total_tokens=1_000_000,
        cache_hit=0,
        cache_miss=1_000_000,
        reasoning=None,
        cost_nanos=cost_nanos,
        currency="USD",
        tariff_cache_hit=Decimal("0.006"),
        tariff_cache_miss=tariff_cache_miss,
        tariff_output=Decimal("1.20"),
        tariff_source="https://api-docs.deepseek.com/quick_start/pricing",
        tariff_verified_at=date(2026, 9, 11),
        status="committed",
        created_at=datetime(2026, 9, 12, 11, 0, tzinfo=UTC),
    )


def _write_v1_without_events(path: Path) -> None:
    """Пишет schema_version=1 и контрольную таблицу без usage_events."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version (version) VALUES (1)")
        connection.execute("CREATE TABLE keep_me (id INTEGER)")
        connection.execute("INSERT INTO keep_me (id) VALUES (1)")
        connection.commit()
    finally:
        connection.close()


def _table_names(path: Path) -> set[str]:
    """Возвращает имена пользовательских таблиц файла."""
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}


def _write_foreign_schema(path: Path) -> None:
    """Пишет чужую версию схемы и контрольную таблицу."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version (version) VALUES (99)")
        connection.execute("CREATE TABLE keep_me (id INTEGER)")
        connection.execute("INSERT INTO keep_me (id) VALUES (1)")
        connection.commit()
    finally:
        connection.close()


def _fetch_keep_me(path: Path) -> tuple[int, ...] | None:
    """Читает контрольную строку после отказа открыть журнал."""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT id FROM keep_me").fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return (int(row[0]),)


def _event_columns(path: Path) -> set[str]:
    """Возвращает имена колонок таблицы событий."""
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute("PRAGMA table_info(usage_events)")}
    finally:
        connection.close()
