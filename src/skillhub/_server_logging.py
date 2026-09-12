"""Адаптирует безопасные события процесса к журналированию Uvicorn."""

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

_PROCESS_CODES = {
    "Started server process [%d]": "process_started",
    "Waiting for application startup.": "application_starting",
    "Application startup complete.": "application_started",
    "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)": "listener_started",
    "Uvicorn running on %s://[%s]:%d (Press CTRL+C to quit)": "listener_started",
    "Shutting down": "shutdown_started",
    "Waiting for application shutdown.": "application_stopping",
    "Application shutdown complete.": "application_stopped",
    "Finished server process [%d]": "process_stopped",
}


def utc_timestamp() -> str:
    """Возвращает текущую отметку UTC в стабильном ISO-формате."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _level_name(record: logging.LogRecord) -> str:
    if record.levelno >= logging.ERROR:
        return "error"
    if record.levelno >= logging.WARNING:
        return "warning"
    if record.levelno >= logging.INFO:
        return "info"
    return "debug"


def _diagnostic_code(record: logging.LogRecord) -> str:
    message = record.msg
    if isinstance(message, OSError):
        return "bind_failure"
    if isinstance(message, str):
        known_code = _PROCESS_CODES.get(message)
        if known_code is not None:
            return known_code
        if message[:48].startswith("error while attempting to bind on address"):
            return "bind_failure"
    return "server_failure" if record.levelno >= logging.ERROR else "server_event"


class SafeServerFormatter(logging.Formatter):
    """Представляет события Uvicorn без исходного сообщения и трассировки."""

    def format(self, record: logging.LogRecord) -> str:
        """Формирует безопасное событие процесса.

        Args:
            record: запись стандартного модуля logging.

        Returns:
            JSON с UTC-временем и стабильным диагностическим кодом.
        """
        code = _diagnostic_code(record)
        is_failure = code in {"bind_failure", "server_failure"}
        event: dict[str, str] = {
            "diagnostic_code": code,
            "event": "server.failure" if is_failure else "server.lifecycle",
            "level": _level_name(record),
            "timestamp": utc_timestamp(),
        }
        if is_failure:
            event["error_type"] = "OSError" if code == "bind_failure" else "ServerError"
        return json.dumps(
            event,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def build_server_log_config() -> dict[str, Any]:
    """Возвращает конфигурацию Uvicorn без журнала доступа и трассировки."""
    safe_logger = {
        "handlers": ["safe"],
        "level": "INFO",
        "propagate": False,
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"safe": {"()": SafeServerFormatter}},
        "handlers": {
            "safe": {
                "class": "logging.StreamHandler",
                "formatter": "safe",
                "stream": "ext://sys.stderr",
            }
        },
        "loggers": {
            "uvicorn": {**safe_logger},
            "uvicorn.error": {**safe_logger},
            "uvicorn.access": {
                "handlers": [],
                "level": "CRITICAL",
                "propagate": False,
            },
        },
    }


def configure_server_logging() -> None:
    """Применяет тот же контракт к объекту main:app, загружаемому извне."""
    logging.config.dictConfig(build_server_log_config())
