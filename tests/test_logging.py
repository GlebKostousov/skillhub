"""Проверяет безопасную конфигурацию структурированных логов."""

import json
import logging
import tracemalloc
from collections.abc import ItemsView, Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
import structlog
from structlog.typing import EventDict, WrappedLogger

from skillhub._server import SafeServerFormatter, build_server_log_config
from skillhub.core import configure_logging
from skillhub.core.logging import redact_sensitive_fields

_MAX_FIELDS = 16
_MAX_OUTPUT_BYTES = 8_192


class _CountingList(list[object]):
    """Считает реально запрошенные элементы широкого контейнера."""

    visited: int = 0

    def __iter__(self) -> Iterator[object]:
        """Возвращает элементы и учитывает каждый шаг обхода."""
        for item in super().__iter__():
            self.visited += 1
            yield item


class _CountingItemsView(ItemsView[object, object]):
    """Считает элементы словаря при фактическом обходе."""

    def __init__(self, owner: "_CountingDict") -> None:
        """Сохраняет словарь-владелец счётчика.

        Args:
            owner: тестовый словарь с числом посещённых элементов.
        """
        super().__init__(owner)
        self._owner = owner

    def __iter__(self) -> Iterator[tuple[object, object]]:
        """Возвращает пары и учитывает каждый шаг обхода."""
        for pair in dict.items(self._owner):
            self._owner.visited += 1
            yield pair


class _CountingDict(dict[object, object]):
    """Считает реально запрошенные пары широкого словаря."""

    visited: int = 0

    def items(self) -> ItemsView[object, object]:  # type: ignore[override]
        """Возвращает представление пар с подсчётом обхода."""
        return _CountingItemsView(self)


class _UnsupportedValue:
    """Неподдерживаемое тестовое значение."""


def _redact_for_test(event: EventDict) -> EventDict:
    """Применяет production-processor к подготовленному событию.

    Args:
        event: проверяемое структурированное событие.

    Returns:
        Ограниченное безопасное событие.
    """
    logger = cast("WrappedLogger", structlog.get_logger())
    return redact_sensitive_fields(logger, "info", event)


def test_json_logging_redacts_unknown_top_level_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет зачистку неизвестных полей без обхода их значений."""
    configure_logging("test")
    private_value = "private-value-behind-unknown-field-9164"
    nested_mapping = _CountingDict({"private": private_value})
    nested_sequence = _CountingList([private_value])

    structlog.get_logger().info(
        "request.checked",
        api_key="provider-secret",
        deepseek_api_key="provider-prefixed-secret",
        nested=nested_mapping,
        records=nested_sequence,
        material="private user text",
        safe_count=3,
    )

    captured = capsys.readouterr()
    event = json.loads(captured.out)
    assert event["api_key"] == "[REDACTED]"
    assert event["deepseek_api_key"] == "[REDACTED]"
    assert event["nested"] == "[REDACTED]"
    assert event["records"] == "[REDACTED]"
    assert event["material"] == "[REDACTED]"
    assert event["safe_count"] == "[REDACTED]"
    assert "provider-secret" not in captured.out
    assert "provider-prefixed-secret" not in captured.out
    assert private_value not in captured.out
    assert "private user text" not in captured.out
    assert nested_mapping.visited == 0
    assert nested_sequence.visited == 0


def test_logging_does_not_guess_fields_from_future_slices(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет отклонение полей будущих прикладных слайсов."""
    configure_logging("test")
    future_fields = {
        "answer": "answer-value",
        "clarification": "clarification-value",
        "intent": "intent-value",
        "prompt": "prompt-value",
        "protocol_draft": "protocol-draft-value",
        "transcription": "transcription-value",
        "transcript": "transcript-value",
    }

    structlog.get_logger().info("current-slice.checked", **future_fields)

    output = capsys.readouterr().out
    event = json.loads(output)
    assert {field: event[field] for field in future_fields} == dict.fromkeys(
        future_fields,
        "[REDACTED]",
    )
    assert event["event"] == "current-slice.checked"
    assert event["level"] == "info"
    assert all(value not in output for value in future_fields.values())


def test_logging_does_not_traverse_cycles_or_repeated_references(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет зачистку циклов и общих ссылок без графовой семантики."""
    configure_logging("test")
    shared = _CountingDict({"safe": "value"})
    cyclic = _CountingList([shared, shared])
    cyclic.append(cyclic)

    structlog.get_logger().info(
        "graph.checked",
        payload=cyclic,
        tuple_payload=(1, 2),
    )

    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["payload"] == "[REDACTED]"
    assert event["tuple_payload"] == "[REDACTED]"
    assert "value" not in output
    assert "[REFERENCE]" not in output
    assert shared.visited == 0
    assert cyclic.visited == 0


def test_logging_keeps_only_supported_bounded_diagnostic_scalars() -> None:
    """Проверяет узкий контракт диагностических скаляров."""
    event = _redact_for_test(
        {
            "event": "x" * 100_000,
            "error_code": None,
            "status_code": 500,
            "error_type": 1.5,
            "request_id": True,
            "level": "info",
            "timestamp": "2026-09-11T19:20:00Z",
        },
    )

    assert str(event["event"]).endswith("[TRUNCATED]")
    assert len(str(event["event"])) < 1_000
    assert event["error_code"] is None
    assert event["status_code"] == 500
    assert event["error_type"] == 1.5
    assert event["request_id"] is True
    assert event["level"] == "info"
    assert event["timestamp"] == "2026-09-11T19:20:00Z"


def test_logging_rejects_unsupported_diagnostic_values_without_traversal() -> None:
    """Проверяет безопасное отклонение объектов и контейнеров."""
    private_value = "private-value-behind-unsupported-value-4826"
    nested_mapping = _CountingDict({"private": private_value})
    nested_sequence = _CountingList([private_value])
    unsupported_values = (
        1 << 100,
        float("nan"),
        _UnsupportedValue(),
        nested_mapping,
        nested_sequence,
        (private_value,),
        {private_value},
    )

    for value in unsupported_values:
        result = _redact_for_test(
            {"event": "unsupported.checked", "error_code": value},
        )
        assert result["error_code"] == "[UNSUPPORTED]"
        assert private_value not in repr(result)

    assert nested_mapping.visited == 0
    assert nested_sequence.visited == 0


def test_logging_enforces_strict_output_byte_cap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет строгий предел итогового JSON в байтах."""
    configure_logging("test")

    structlog.get_logger().info(
        "\U0010ffff" * 100_000,
        error_code="\U0010ffff" * 100_000,
        error_type="\U0010ffff" * 100_000,
        request_id="\U0010ffff" * 100_000,
    )

    output = capsys.readouterr().out
    event = json.loads(output)
    assert len(output.encode("utf-8")) <= _MAX_OUTPUT_BYTES
    assert event == {"event": "log.truncated", "truncation": "[TRUNCATED]"}


def test_logging_bounds_work_for_wide_mapping_and_long_text() -> None:
    """Проверяет память обхода после получения больших контейнеров."""
    wide_event: EventDict = {
        "event": "wide.checked",
        **{f"field-{index}": "value" for index in range(100_000)},
    }
    long_text = "x" * 2_000_000

    tracemalloc.start()
    try:
        wide_result = _redact_for_test(wide_event)
        _, wide_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        text_result = _redact_for_test({"event": "text.checked", "payload": long_text})
        _, text_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert wide_result["event"] == "wide.checked"
    assert text_result["event"] == "text.checked"
    assert wide_peak < 1_000_000
    assert text_peak < 1_000_000


def test_logging_bounds_number_of_visited_sequence_items() -> None:
    """Проверяет отсутствие обхода неизвестного контейнера."""
    wide_sequence = _CountingList(range(100_000))

    result = _redact_for_test(
        {"event": "visited.checked", "payload": wide_sequence},
    )

    assert result["event"] == "visited.checked"
    assert result["payload"] == "[REDACTED]"
    assert wide_sequence.visited == 0
    assert "[REFERENCE]" not in repr(result)


def test_logging_bounds_unsupported_mapping_key_traversal() -> None:
    """Проверяет предел обхода словаря с неподдерживаемыми ключами."""
    private_value = "private-value-behind-unsupported-keys-7194"
    wide_mapping = _CountingDict({"event": "mapping-visited.checked"})
    wide_mapping.update((object(), private_value) for _ in range(100_000))

    result = _redact_for_test(cast("EventDict", wide_mapping))

    assert result["event"] == "mapping-visited.checked"
    assert wide_mapping.visited <= _MAX_FIELDS
    assert private_value not in repr(result)
    assert "[UNSUPPORTED]" in repr(result)
    assert "[TRUNCATED]" in repr(result)


def test_server_formatter_uses_safe_process_codes_and_utc_time() -> None:
    """Проверяет коды процесса без исходного сообщения и аргументов."""
    private_marker = "private-server-log-content-7312"
    exception = RuntimeError(private_marker)
    exception_info = (type(exception), exception, exception.__traceback__)
    records = [
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Waiting for application startup.",
            args=(private_marker,),
            exc_info=None,
        ),
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Application startup complete.",
            args=(private_marker,),
            exc_info=None,
        ),
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Shutting down",
            args=(private_marker,),
            exc_info=None,
        ),
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=OSError(private_marker),
            args=(),
            exc_info=exception_info,
        ),
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=private_marker,
            args=(),
            exc_info=None,
        ),
        logging.LogRecord(
            name="uvicorn.error",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=private_marker,
            args=(),
            exc_info=exception_info,
        ),
    ]

    outputs = [SafeServerFormatter().format(record) for record in records]
    events = [json.loads(output) for output in outputs]

    assert [event["diagnostic_code"] for event in events] == [
        "application_starting",
        "application_started",
        "shutdown_started",
        "bind_failure",
        "server_event",
        "server_failure",
    ]
    assert len({event["diagnostic_code"] for event in events}) == len(events)
    for event in events:
        timestamp = str(event["timestamp"])
        assert timestamp.endswith("Z")
        assert datetime.fromisoformat(timestamp).tzinfo == UTC
    assert private_marker not in "".join(outputs)
    assert "traceback" not in "".join(outputs).lower()
    assert build_server_log_config()["loggers"]["uvicorn.access"]["handlers"] == []
