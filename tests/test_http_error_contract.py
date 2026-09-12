"""Проверяет единый публичный контракт системных HTTP-ошибок."""

import pytest
from fastapi.testclient import TestClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.app_factory import create_app


@pytest.mark.parametrize(
    ("method", "path", "headers", "expected"),
    [
        (
            "GET",
            "/",
            {"host": "attacker.example"},
            (400, "bad_request", "Некорректный запрос."),
        ),
        (
            "GET",
            "/private-missing-path-21",
            {},
            (404, "not_found", "Запрошенные данные не найдены."),
        ),
        (
            "POST",
            "/health",
            {},
            (405, "method_not_allowed", "Метод запроса не поддерживается."),
        ),
    ],
)
def test_framework_failure_uses_skillhub_error_envelope(
    method: str,
    path: str,
    headers: dict[str, str],
    expected: tuple[int, str, str],
) -> None:
    """Проверяет типизированную безопасную оболочку транспортного отказа."""
    app = create_app()
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = TestClient(app).request(method, path, headers=headers)
    status_code, code, message = expected

    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")

    payload: object = response.json()
    assert isinstance(payload, dict)
    assert set(payload) == {"error"}
    error = payload["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message"}
    assert error["code"] == code
    assert error["message"] == message
    assert path not in response.text
    if host := headers.get("host"):
        assert host not in response.text
    assert logs[0]["error_code"] == code
    assert logs[0]["status_code"] == status_code
    assert logs[0]["request_id"] == response.headers["x-request-id"]
    assert path not in repr(logs)
    if host := headers.get("host"):
        assert host not in repr(logs)
