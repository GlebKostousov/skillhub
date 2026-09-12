"""Проверяет документированный запуск отдельного процесса SkillHub."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

import skillhub.__main__ as runtime
import skillhub._server as server_runtime

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_START_COMMAND_PATTERN = re.compile(
    r"```console\r?\n(uv run python -m skillhub [^\r\n]+)\r?\n```"
)


def _documented_start_command() -> list[str]:
    """Извлекает единственную команду запуска из README.

    Returns:
        Токены документированной команды запуска.
    """
    readme = (_REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    matches = [str(match) for match in _START_COMMAND_PATTERN.findall(readme)]
    assert len(matches) == 1
    return matches[0].split()


def _unused_local_port() -> int:
    """Выбирает свободный локальный TCP-порт для тестового процесса.

    Returns:
        Номер порта, свободного на момент проверки.
    """
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]
    return int(port)


def _runtime_start_command(port: int) -> list[str]:
    """Подготавливает документированную команду для тестового порта.

    Args:
        port: свободный локальный порт.

    Returns:
        Команда запуска без синхронизации зависимостей.
    """
    command = _documented_start_command()
    assert command[:5] == ["uv", "run", "python", "-m", "skillhub"]

    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.fail("uv не найден в PATH.")
    command[0] = uv_executable
    command[command.index("--port") + 1] = str(port)
    command.insert(2, "--no-sync")
    return command


def _published_uvicorn_command(port: int) -> list[str]:
    """Подготавливает стандартную загрузку опубликованного ASGI-приложения.

    Args:
        port: свободный локальный порт.

    Returns:
        Команда Uvicorn без специальных параметров журналирования.
    """
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.fail("uv не найден в PATH.")
    return [
        uv_executable,
        "run",
        "--no-sync",
        "uvicorn",
        "skillhub.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _invalid_runtime_command(*arguments: str) -> list[str]:
    """Подготавливает модуль запуска с заведомо ошибочными аргументами.

    Args:
        arguments: ошибочные аргументы модуля запуска.

    Returns:
        Команда запуска без синхронизации зависимостей.
    """
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.fail("uv не найден в PATH.")
    return [
        uv_executable,
        "run",
        "--no-sync",
        "python",
        "-m",
        "skillhub",
        *arguments,
    ]


def _runtime_error_command(port: int, private_marker: str) -> list[str]:
    """Подготавливает процесс с ошибочным маршрутом для проверки границы.

    Args:
        port: свободный локальный порт.
        private_marker: пользовательское содержимое исключения.

    Returns:
        Команда запуска тестового приложения через штатную границу процесса.
    """
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.fail("uv не найден в PATH.")
    script = f"""
from skillhub._server import run_server
from skillhub.app_factory import create_app

app = create_app()

def fail() -> None:
    raise RuntimeError({private_marker!r})

app.add_api_route("/unexpected-error", fail)
run_server(app, port={port})
"""
    return [uv_executable, "run", "--no-sync", "python", "-c", script]


def _runtime_late_error_command(port: int, private_marker: str) -> list[str]:
    """Подготавливает процесс с ошибкой после начала HTTP-ответа.

    Args:
        port: свободный локальный порт.
        private_marker: пользовательское содержимое исключения.

    Returns:
        Команда запуска тестового приложения через границу процесса.
    """
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.fail("uv не найден в PATH.")
    script = f"""
from collections.abc import Iterator

from fastapi.responses import StreamingResponse

from skillhub._server import run_server
from skillhub.app_factory import create_app

app = create_app()

def fail_late() -> Iterator[bytes]:
    yield b"partial"
    raise RuntimeError({private_marker!r})

def late_response() -> StreamingResponse:
    return StreamingResponse(
        fail_late(),
        media_type="text/plain",
        headers={{"content-length": "16"}},
    )

app.add_api_route("/late-error", late_response)
run_server(app, port={port})
"""
    return [uv_executable, "run", "--no-sync", "python", "-c", script]


def _provider_free_environment() -> dict[str, str]:
    """Возвращает окружение без настроек SkillHub и DeepSeek.

    Returns:
        Копия окружения без конфигурации приложения и провайдера.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DEEPSEEK_", "SKILLHUB_"))
    }


def _json_events(output: str) -> list[dict[str, object]]:
    """Разбирает каждую непустую строку журнала процесса как JSON-событие.

    Args:
        output: объединённый вывод проверяемого процесса.

    Returns:
        Последовательность машинных событий процесса.
    """
    events: list[dict[str, object]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        payload: object = json.loads(line)
        assert isinstance(payload, dict)
        events.append(payload)
    return events


def _assert_utc_timestamps(events: list[dict[str, object]]) -> None:
    """Проверяет наличие корректного UTC-времени у машинных событий."""
    assert events
    for event in events:
        timestamp = event.get("timestamp")
        assert isinstance(timestamp, str)
        assert timestamp.endswith("Z")
        assert datetime.fromisoformat(timestamp).tzinfo == UTC


def _wait_for_health(process: subprocess.Popen[str], port: int) -> None:
    """Ожидает точный health-контракт или ранний отказ процесса.

    Args:
        process: процесс, запущенный документированной командой.
        port: локальный порт тестового процесса.
    """
    deadline = time.monotonic() + 20
    health_url = f"http://127.0.0.1:{port}/health"

    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"Код завершения команды запуска: {process.returncode}.")
        try:
            with urlopen(health_url, timeout=0.5) as response:
                payload: object = json.loads(response.read())
        except URLError:
            time.sleep(0.05)
        else:
            assert response.status == 200
            assert payload == {"status": "ok"}
            return

    pytest.fail("Документированная команда не открыла /health за 20 секунд.")


def _stop_process(process: subprocess.Popen[str]) -> None:
    """Завершает процесс запуска и его дочерний сервер.

    Args:
        process: процесс документированной команды.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            process.kill()
        else:
            subprocess.run(  # noqa: S603
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_readme_command_starts_local_app_without_provider_environment() -> None:
    """Проверяет отдельный запуск на localhost без настроек DeepSeek."""
    port = _unused_local_port()
    process = subprocess.Popen(  # noqa: S603
        _runtime_start_command(port),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(process, port)
    finally:
        _stop_process(process)


def test_uvicorn_access_log_does_not_include_raw_query_content() -> None:
    """Проверяет отсутствие параметра запроса в журнале доступа."""
    port = _unused_local_port()
    private_marker = "private-query-content-9173"
    process = subprocess.Popen(  # noqa: S603
        _runtime_start_command(port),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(process, port)
        with urlopen(
            f"http://127.0.0.1:{port}/health?material={private_marker}",
            timeout=1,
        ) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
    finally:
        _stop_process(process)

    output, _ = process.communicate(timeout=5)
    assert private_marker not in output
    events = _json_events(output)
    _assert_utc_timestamps(events)
    assert [event.get("diagnostic_code") for event in events[:4]] == [
        "process_started",
        "application_starting",
        "application_started",
        "listener_started",
    ]


def test_published_uvicorn_app_does_not_log_raw_query_content() -> None:
    """Проверяет журнал доступа при стандартной загрузке main:app."""
    port = _unused_local_port()
    private_marker = "private-main-entry-query-4821"
    process = subprocess.Popen(  # noqa: S603
        _published_uvicorn_command(port),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(process, port)
        with urlopen(
            f"http://127.0.0.1:{port}/health?material={private_marker}",
            timeout=1,
        ) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
    finally:
        _stop_process(process)

    output, _ = process.communicate(timeout=5)
    assert private_marker not in output
    events = _json_events(output)
    _assert_utc_timestamps(events)
    assert [event.get("diagnostic_code") for event in events[:4]] == [
        "process_started",
        "application_starting",
        "application_started",
        "listener_started",
    ]


def test_occupied_port_emits_distinct_safe_bind_lifecycle() -> None:
    """Проверяет реальный bind-отказ и различимые lifecycle-события."""
    with socket.socket() as occupied_socket:
        occupied_socket.bind(("127.0.0.1", 0))
        occupied_socket.listen()
        occupied_port = int(occupied_socket.getsockname()[1])
        result = subprocess.run(  # noqa: S603
            _runtime_start_command(occupied_port),
            cwd=_REPOSITORY_ROOT,
            env=_provider_free_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    output = f"{result.stdout}\n{result.stderr}"
    events = _json_events(output)
    _assert_utc_timestamps(events)
    diagnostic_codes = [
        code
        for event in events
        if isinstance(code := event.get("diagnostic_code"), str)
    ]

    assert result.returncode != 0
    assert len(diagnostic_codes) == len(set(diagnostic_codes))
    assert {
        "process_started",
        "application_starting",
        "application_started",
        "bind_failure",
        "application_stopping",
        "application_stopped",
    }.issubset(diagnostic_codes)
    bind_event = next(
        event for event in events if event.get("diagnostic_code") == "bind_failure"
    )
    assert bind_event == {
        "diagnostic_code": "bind_failure",
        "error_type": "OSError",
        "event": "server.failure",
        "level": "error",
        "timestamp": bind_event["timestamp"],
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ("--port", "private-invalid-port-9137"),
        ("--private-unknown-argument-2751",),
    ],
)
def test_invalid_cli_arguments_use_fixed_safe_event(
    arguments: tuple[str, ...],
) -> None:
    """Проверяет отказ CLI без отражения исходных argv и usage."""
    private_marker = arguments[-1]

    result = subprocess.run(  # noqa: S603
        _invalid_runtime_command(*arguments),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert private_marker not in output
    assert "traceback" not in output.lower()
    assert "usage:" not in output.lower()
    assert "startup.cli_error" in output
    assert "invalid_arguments" in output
    events = _json_events(output)
    _assert_utc_timestamps(events)
    assert len(events) == 1
    assert events[0] == {
        "error_code": "invalid_arguments",
        "error_type": "ArgumentError",
        "event": "startup.cli_error",
        "timestamp": events[0]["timestamp"],
    }


def test_startup_error_log_redacts_invalid_configuration_value() -> None:
    """Проверяет безопасный лог отказа при неверной конфигурации."""
    private_marker = "private-invalid-environment-7284"
    environment = _provider_free_environment()
    environment["SKILLHUB_ENVIRONMENT"] = private_marker

    result = subprocess.run(  # noqa: S603
        _runtime_start_command(_unused_local_port()),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert private_marker not in output
    assert "traceback" not in output.lower()
    assert "startup.configuration_error" in output
    assert "invalid_configuration" in output
    assert "ValidationError" in output
    events = _json_events(output)
    _assert_utc_timestamps(events)
    assert len(events) == 1
    assert events[0] == {
        "error_code": "invalid_configuration",
        "error_type": "ValidationError",
        "event": "startup.configuration_error",
        "timestamp": events[0]["timestamp"],
    }


@pytest.mark.parametrize(
    "variable_name",
    [
        "SKILLHUB_ENVIRONMENT",
        "SKILLHUB_PRIVATE_UNKNOWN_SETTING_2751",
    ],
)
def test_published_uvicorn_app_uses_safe_startup_boundary(
    variable_name: str,
) -> None:
    """Проверяет безопасный отказ стандартной загрузки Uvicorn.

    Args:
        variable_name: неверная или неизвестная переменная SkillHub.
    """
    private_marker = "private-published-config-value-9361"
    environment = _provider_free_environment()
    environment[variable_name] = private_marker

    result = subprocess.run(  # noqa: S603
        _published_uvicorn_command(_unused_local_port()),
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert private_marker not in output
    assert "traceback" not in output.lower()
    assert str(_REPOSITORY_ROOT).lower() not in output.lower()
    events = _json_events(output)
    _assert_utc_timestamps(events)
    assert len(events) == 1
    assert events[0] == {
        "error_code": "invalid_configuration",
        "error_type": "ValidationError",
        "event": "startup.configuration_error",
        "timestamp": events[0]["timestamp"],
    }


def test_runtime_main_exits_with_safe_configuration_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет безопасный отказ публичной функции запуска."""
    private_marker = "private-main-environment-2457"
    monkeypatch.setenv("SKILLHUB_ENVIRONMENT", private_marker)
    monkeypatch.setattr(sys, "argv", ["skillhub", "--port", "8000"])

    with pytest.raises(SystemExit) as captured:
        runtime.main()

    output = capsys.readouterr().err
    assert captured.value.code == 2
    assert private_marker not in output
    assert "traceback" not in output.lower()
    assert "startup.configuration_error" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["skillhub", "--port", "private-unit-port-1864"],
        ["skillhub", "--port", "0"],
    ],
)
def test_runtime_main_rejects_cli_before_application_build(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """Проверяет единую границу запуска для ошибок argparse и диапазона."""
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as captured:
        runtime.main()

    output = capsys.readouterr().err
    assert captured.value.code == 2
    if argv[-1].startswith("private-"):
        assert argv[-1] not in output
    assert "startup.cli_error" in output
    assert "invalid_arguments" in output


def test_runtime_main_redacts_unexpected_build_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет безопасный общий отказ запуска корня сборки."""
    private_marker = "private-build-failure-4827"

    def fail_build() -> None:
        """Поднимает тестовый отказ без запуска сервера."""
        raise RuntimeError(private_marker)

    monkeypatch.setattr(sys, "argv", ["skillhub", "--port", "8000"])
    monkeypatch.setattr(server_runtime, "create_app", fail_build)

    with pytest.raises(SystemExit) as captured:
        runtime.main()

    output = capsys.readouterr().err
    assert captured.value.code == 2
    assert private_marker not in output
    assert "startup.application_error" in output
    assert "startup_failure" in output


def test_runtime_main_delegates_valid_app_to_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет тонкое делегирование исправного модуля запуска."""
    observed_ports: list[int] = []

    def record_server(_app: object, *, port: int) -> None:
        """Запоминает порт без открытия сокета."""
        observed_ports.append(port)

    monkeypatch.setattr(sys, "argv", ["skillhub", "--port", "8123"])
    monkeypatch.setattr(server_runtime, "run_server", record_server)

    runtime.main()

    assert observed_ports == [8123]


def test_runtime_error_log_keeps_safe_type_code_and_request_id() -> None:
    """Проверяет безопасную корреляцию неожиданной ошибки процесса."""
    port = _unused_local_port()
    private_marker = "private-runtime-process-content-4198"
    process = subprocess.Popen(  # noqa: S603
        _runtime_error_command(port, private_marker),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    request_id = ""
    try:
        _wait_for_health(process, port)
        with pytest.raises(HTTPError) as captured:
            urlopen(f"http://127.0.0.1:{port}/unexpected-error", timeout=1)
        response = captured.value
        assert response.code == 500
        assert json.loads(response.read()) == {
            "error": {
                "code": "internal_error",
                "message": "Внутренняя ошибка сервера.",
            }
        }
        request_id = response.headers["x-request-id"]
    finally:
        _stop_process(process)

    output, _ = process.communicate(timeout=5)
    assert private_marker not in output
    assert "traceback" not in output.lower()
    assert "http.unexpected_error" in output
    assert "RuntimeError" in output
    assert "internal_error" in output
    assert request_id in output
    events = _json_events(output)
    _assert_utc_timestamps(events)
    unexpected_events = [
        event for event in events if event.get("event") == "http.unexpected_error"
    ]
    assert len(unexpected_events) == 1
    assert unexpected_events[0]["error_code"] == "internal_error"
    assert unexpected_events[0]["error_type"] == "RuntimeError"
    assert unexpected_events[0]["request_id"] == request_id


def test_runtime_late_error_aborts_with_correlated_safe_event() -> None:
    """Проверяет обрыв позднего ответа и безопасную корреляцию события."""
    port = _unused_local_port()
    private_marker = "private-late-runtime-content-5731"
    process = subprocess.Popen(  # noqa: S603
        _runtime_late_error_command(port, private_marker),
        cwd=_REPOSITORY_ROOT,
        env=_provider_free_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    request_id = ""
    output = ""
    try:
        _wait_for_health(process, port)
        with urlopen(f"http://127.0.0.1:{port}/late-error", timeout=1) as response:
            assert response.status == 200
            request_id = response.headers["x-request-id"]
            with pytest.raises(IncompleteRead):
                response.read()
    finally:
        _stop_process(process)
        output, _ = process.communicate(timeout=5)

    assert private_marker not in output
    assert "traceback" not in output.lower()
    events = _json_events(output)
    _assert_utc_timestamps(events)
    late_events = [
        event for event in events if event.get("event") == "http.response_aborted"
    ]
    assert len(late_events) == 1
    assert late_events[0]["error_code"] == "response_aborted"
    assert late_events[0]["error_type"] == "RuntimeError"
    assert late_events[0]["request_id"] == request_id
    assert any(event.get("diagnostic_code") == "server_failure" for event in events)
