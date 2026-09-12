"""Настраивает безопасные структурированные логи приложения."""

import logging

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from skillhub.core._redaction import (
    MAX_OUTPUT_BYTES,
    redact_sensitive_fields,
    render_bounded_json,
)
from skillhub.core.settings import Environment

__all__ = ["MAX_OUTPUT_BYTES", "redact_sensitive_fields"]


def configure_logging(environment: Environment) -> None:
    """Настраивает машинное представление структурированных логов.

    Args:
        environment: проверенный режим процесса.
    """
    del environment
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_fields,
            render_bounded_json,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def bind_request_id(request_id: str) -> None:
    """Привязывает идентификатор к текущему контексту выполнения.

    Args:
        request_id: сгенерированный приложением идентификатор запроса.
    """
    clear_contextvars()
    bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    """Очищает контекст логирования после обработки запроса."""
    clear_contextvars()
