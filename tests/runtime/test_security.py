"""Проверяет отказ секретов и путей в runtime-наложении."""

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from skillhub.runtime import InvalidOverlayError, RuntimeStore

_LIMITS = {"deepseek-flash": 16384}
_OVERLAY_NAME = "runtime-overlay.json"
_LEAK_MARKER = "sk-leak-marker-must-stay-out"
_FORBIDDEN = (
    "api_key",
    "provider_credential",
    "environment",
    "usage_path",
    "overlay_path",
)


def test_secrets_and_boot_paths_are_rejected_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ секретов и путей без утечки значения."""
    monkeypatch.chdir(tmp_path)
    store = RuntimeStore(_LIMITS)
    payload = _payload()

    for name in _FORBIDDEN:
        with pytest.raises(InvalidOverlayError) as captured:
            store.save({**payload, name: _LEAK_MARKER})
        observed = f"{captured.value!s}{captured.value!r}"
        assert captured.value.code == "invalid_overlay"
        assert _LEAK_MARKER not in observed

    assert not (tmp_path / _OVERLAY_NAME).exists()
    names = {field.name for field in store.snapshot().fields}
    assert names.isdisjoint(_FORBIDDEN)


def test_store_rejects_user_supplied_path() -> None:
    """Проверяет, что путь файла не принимается конструктором."""
    with pytest.raises(TypeError):
        RuntimeStore(_LIMITS, path=_OVERLAY_NAME)  # type: ignore[call-arg]


def test_corrupt_secret_file_does_not_leak_into_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отсутствие секрета повреждённого файла в логах."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / _OVERLAY_NAME).write_text(
        json.dumps({"api_key": _LEAK_MARKER}),
        encoding="utf-8",
    )
    store = RuntimeStore(_LIMITS)

    with capture_logs() as logs:
        snapshot = store.snapshot()

    assert snapshot.values.model == "deepseek-flash"
    assert _LEAK_MARKER not in repr(logs)
    assert logs[0]["error_code"] == "invalid_overlay"


def _payload() -> dict[str, object]:
    return {
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
