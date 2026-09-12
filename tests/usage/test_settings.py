"""Проверяет путь журнала расходов в Settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from skillhub.core.settings import Settings


def test_usage_path_defaults_to_sqlite3() -> None:
    """Проверяет значение по умолчанию для пути журнала."""
    assert Settings().usage_path == Path("usage.sqlite3")


def test_usage_path_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет чтение пути журнала из окружения."""
    monkeypatch.setenv("SKILLHUB_USAGE_PATH", "custom.sqlite3")

    assert Settings().usage_path == Path("custom.sqlite3")


def test_daily_budget_defaults_to_unlimited() -> None:
    """Проверяет отсутствие дневного потолка по умолчанию."""
    assert Settings().daily_budget_nanos is None


def test_unknown_usage_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ неизвестной переменной SKILLHUB после полей usage."""
    monkeypatch.setenv("SKILLHUB_USAGE_UNKNOWN", "enabled")

    with pytest.raises(ValidationError) as captured:
        Settings()

    assert "enabled" not in str(captured.value)
