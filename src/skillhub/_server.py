"""Владеет безопасным запуском локального Uvicorn."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import NoReturn

import uvicorn
from pydantic import ValidationError
from starlette.types import ASGIApp

from skillhub._server_logging import (
    SafeServerFormatter,
    build_server_log_config,
    utc_timestamp,
)
from skillhub.app_factory import create_app

__all__ = ["SafeServerFormatter", "build_server_log_config"]

_MAX_PORT = 65_535


class _CliArgumentsError(Exception):
    """Ошибка безопасной проверки аргументов модуля запуска."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Парсер аргументов без автоматического отражения argv."""

    def error(self, _message: str) -> NoReturn:
        """Завершает разбор типизированной внутренней ошибкой.

        Args:
            _message: неиспользуемое сообщение argparse с исходным argv.

        Raises:
            _CliArgumentsError: всегда для неверных аргументов.
        """
        raise _CliArgumentsError


def _parse_port(arguments: Sequence[str] | None = None) -> int:
    """Возвращает проверенный локальный порт без отражения неверного argv."""
    parser = _SafeArgumentParser(description="Локальный сервер SkillHub.")
    parser.add_argument("--port", type=int, default=8000)
    parsed = parser.parse_args(arguments)
    port = int(parsed.port)
    if not 1 <= port <= _MAX_PORT:
        raise _CliArgumentsError
    return port


def _write_startup_failure(exc: Exception) -> None:
    """Записывает фиксированное событие запуска без значения ошибки."""
    if isinstance(exc, _CliArgumentsError):
        event_name = "startup.cli_error"
        error_code = "invalid_arguments"
        error_type = "ArgumentError"
    elif isinstance(exc, ValidationError):
        event_name = "startup.configuration_error"
        error_code = "invalid_configuration"
        error_type = "ValidationError"
    else:
        event_name = "startup.application_error"
        error_code = "startup_failure"
        error_type = type(exc).__name__[:64]
    event = {
        "error_code": error_code,
        "error_type": error_type,
        "event": event_name,
        "timestamp": utc_timestamp(),
    }
    rendered = json.dumps(
        event,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    sys.stderr.write(f"{rendered}\n")


def create_process_app() -> ASGIApp:
    """Возвращает приложение через безопасную границу запуска процесса.

    Returns:
        Приложение, собранное единственным корнем композиции.

    Raises:
        SystemExit: ошибка сборки завершает процесс после безопасного события.
    """
    try:
        return create_app()
    except Exception as exc:  # noqa: BLE001
        _write_startup_failure(exc)
        raise SystemExit(2) from None


def run_server(app: ASGIApp, *, port: int) -> None:
    """Запускает локальный Uvicorn с единым безопасным журналированием."""
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_config=build_server_log_config(),
    )


def main() -> None:
    """Передаёт проверенные параметры запуска единственному корню сборки."""
    try:
        port = _parse_port()
    except Exception as exc:  # noqa: BLE001
        # Граница процесса намеренно не форматирует значение исключения.
        _write_startup_failure(exc)
        raise SystemExit(2) from None
    app = create_process_app()
    run_server(app, port=port)
