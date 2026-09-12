"""Проверяет посев, схему и атомарную запись runtime-настроек."""

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from skillhub.runtime import (
    InvalidOverlayError,
    OverlayUnavailableError,
    RuntimeStore,
)

_LIMITS = {"deepseek-flash": 16384, "deepseek-reasoner": 8192}
_OVERLAY_NAME = "runtime-overlay.json"
_EDITABLE = (
    "model",
    "max_tokens",
    "temperature",
    "timeout",
    "stream",
    "thinking",
    "reasoning_effort",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "response_format",
    "daily_budget_nanos",
)


def test_seed_catalog_exposes_hints_defaults_and_kinds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет полный каталог полей из посева констант и бюджета."""
    monkeypatch.chdir(tmp_path)
    snapshot = RuntimeStore(_LIMITS, daily_budget_nanos=250).snapshot()
    names = tuple(field.name for field in snapshot.fields)
    by_name = {field.name: field for field in snapshot.fields}

    assert names == _EDITABLE
    assert by_name["model"].value == "deepseek-flash"
    assert by_name["model"].default == "deepseek-flash"
    assert by_name["model"].kind == "choice"
    assert by_name["model"].allowed == ("deepseek-flash", "deepseek-reasoner")
    assert by_name["max_tokens"].value == 16384
    assert by_name["max_tokens"].default == 16384
    assert by_name["max_tokens"].kind == "integer"
    assert by_name["max_tokens"].min == 1
    assert by_name["max_tokens"].max == 16384
    assert by_name["temperature"].value == 0
    assert by_name["temperature"].default == 0
    assert by_name["temperature"].kind == "number"
    assert by_name["timeout"].value == 60.0
    assert by_name["timeout"].default == 60.0
    assert by_name["timeout"].kind == "number"
    assert by_name["stream"].value is False
    assert by_name["stream"].default is False
    assert by_name["stream"].allowed == (False,)
    assert by_name["thinking"].value == "enabled"
    assert by_name["thinking"].default == "enabled"
    assert by_name["thinking"].allowed == ("enabled", "disabled")
    assert by_name["reasoning_effort"].value == "high"
    assert by_name["reasoning_effort"].default == "high"
    assert by_name["reasoning_effort"].allowed == ("low", "high", "max")
    assert by_name["top_p"].value == 1.0
    assert by_name["top_p"].default == 1.0
    assert by_name["frequency_penalty"].value == 0
    assert by_name["presence_penalty"].value == 0
    assert by_name["stop"].value == ()
    assert by_name["stop"].default == ()
    assert by_name["stop"].kind == "string_list"
    assert by_name["response_format"].value == "text"
    assert by_name["response_format"].allowed == ("text", "json_object")
    assert by_name["daily_budget_nanos"].value == 250
    assert by_name["daily_budget_nanos"].default == 250
    assert by_name["daily_budget_nanos"].min == 0
    for field in snapshot.fields:
        assert field.hint
        assert any(ord(character) >= 1024 for character in field.hint)


def test_corrupt_file_keeps_seed_and_logs_invalid_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что повреждённый файл не валит процесс и даёт посев."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / _OVERLAY_NAME).write_text("{not-json", encoding="utf-8")
    store = RuntimeStore(_LIMITS, daily_budget_nanos=None)

    with capture_logs() as logs:
        snapshot = store.snapshot()

    assert snapshot.values.model == "deepseek-flash"
    assert snapshot.values.max_tokens == 16384
    assert snapshot.values.daily_budget_nanos is None
    assert logs == [
        {
            "event": "runtime.invalid_overlay",
            "error_code": "invalid_overlay",
            "log_level": "warning",
        }
    ]


def test_unknown_key_stream_true_and_out_of_range_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ лишнего ключа, stream true и значения вне диапазона."""
    monkeypatch.chdir(tmp_path)
    store = RuntimeStore(_LIMITS)

    with pytest.raises(InvalidOverlayError) as extra:
        store.save(_payload(unexpected="enabled"))
    with pytest.raises(InvalidOverlayError) as streamed:
        store.save(_payload(stream=True))
    with pytest.raises(InvalidOverlayError) as tokens:
        store.save(_payload(max_tokens=20000))
    with pytest.raises(InvalidOverlayError) as budget:
        store.save(_payload(daily_budget_nanos=-1))

    assert extra.value.code == "invalid_overlay"
    assert extra.value.status_code == 422
    assert streamed.value.code == "invalid_overlay"
    assert tokens.value.code == "invalid_overlay"
    assert budget.value.code == "invalid_overlay"
    assert not (tmp_path / _OVERLAY_NAME).exists()


def test_successful_save_is_atomic_and_visible_to_next_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет атомарную запись и чтение следующим snapshot()."""
    monkeypatch.chdir(tmp_path)
    store = RuntimeStore(_LIMITS, daily_budget_nanos=None)
    store.save(
        _payload(
            max_tokens=4096,
            temperature=0.2,
            timeout=45.0,
            thinking="disabled",
            reasoning_effort="low",
            top_p=0.8,
            frequency_penalty=0.1,
            presence_penalty=0.2,
            stop=["END"],
            response_format="json_object",
            daily_budget_nanos=500,
        )
    )

    snapshot = store.snapshot()
    leftovers = list(tmp_path.glob("*.tmp"))

    assert leftovers == []
    assert (tmp_path / _OVERLAY_NAME).is_file()
    assert snapshot.values.model == "deepseek-flash"
    assert snapshot.values.max_tokens == 4096
    assert snapshot.values.temperature == 0.2
    assert snapshot.values.timeout == 45.0
    assert snapshot.values.stream is False
    assert snapshot.values.thinking == "disabled"
    assert snapshot.values.reasoning_effort == "low"
    assert snapshot.values.top_p == 0.8
    assert snapshot.values.frequency_penalty == 0.1
    assert snapshot.values.presence_penalty == 0.2
    assert snapshot.values.stop == ("END",)
    assert snapshot.values.response_format == "json_object"
    assert snapshot.values.daily_budget_nanos == 500
    assert snapshot.fields[1].value == 4096
    assert snapshot.fields[1].default == 16384


def test_write_failure_raises_overlay_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ записи с кодом overlay_unavailable."""
    monkeypatch.chdir(tmp_path)
    store = RuntimeStore(_LIMITS)

    def fail_replace(*_arguments: object, **_options: object) -> None:
        raise OSError

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OverlayUnavailableError) as captured:
        store.save(_payload())

    assert captured.value.code == "overlay_unavailable"
    assert captured.value.status_code == 409
    assert not (tmp_path / _OVERLAY_NAME).exists()


def test_unknown_model_and_foreign_ceiling_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ модели вне allowlist и потолка чужого каталога."""
    monkeypatch.chdir(tmp_path)
    store = RuntimeStore(_LIMITS)

    with pytest.raises(InvalidOverlayError):
        store.save(_payload(model="gpt-4"))
    with pytest.raises(InvalidOverlayError):
        store.save(_payload(model="deepseek-reasoner", max_tokens=9000))


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "model": "deepseek-flash",
        "max_tokens": 4096,
        "temperature": 0,
        "timeout": 60.0,
        "stream": False,
        "thinking": "enabled",
        "reasoning_effort": "high",
        "top_p": 1.0,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "stop": [],
        "response_format": "text",
        "daily_budget_nanos": None,
    }
    values.update(overrides)
    return values
