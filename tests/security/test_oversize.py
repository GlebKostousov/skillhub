"""Проверяет отказ при превышении тела без утечки пути, prompt и ключа."""

import asyncio
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Message, Scope
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from skillhub.web._assistant_guard import validate_assistant_request
from skillhub.web._reload_guard import validate_reload_request
from skillhub.web.errors import RequestTooLargeError
from tests.security._leaks import (
    assert_no_secret_leak,
    leak_markers,
)

_KEY, _PROMPT, _ABS_PATH = leak_markers()


def test_oversize_body_rejects_without_path_prompt_or_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет 413 и чистое исключение для oversized и ядовитого размера."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    client = TestClient(create_app())
    token = _csrf_from(client.get("/").text)
    body = _oversized_body()

    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.post(
            "/api/assistant",
            content=body,
            headers={
                "origin": "http://testserver",
                "content-type": "application/json",
                "x-csrf-token": token,
            },
        )
        captured = _invalid_length_error(token)
        reload_error = _invalid_reload_length_error(token)

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "Тело запроса превышает допустимый размер.",
        }
    }
    assert captured.code == "request_too_large"
    assert reload_error.code == "request_too_large"
    assert_no_secret_leak(response.text, logs, captured, reload_error)


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _oversized_body() -> bytes:
    prefix = f"{_ABS_PATH} {_PROMPT} {_KEY} ".encode()
    return prefix + b"x" * (1_048_577 - len(prefix))


def _invalid_length_error(token: str) -> RequestTooLargeError:
    request = Request(
        _poisoned_scope(token, "/api/assistant", "application/json"),
        _empty_receive,
    )
    with pytest.raises(RequestTooLargeError) as captured:
        asyncio.run(validate_assistant_request(request, token))
    return captured.value


def _invalid_reload_length_error(token: str) -> RequestTooLargeError:
    request = Request(
        _poisoned_scope(token, "/skills/reload", "application/x-www-form-urlencoded"),
        _empty_receive,
    )
    with pytest.raises(RequestTooLargeError) as captured:
        asyncio.run(validate_reload_request(request, token))
    return captured.value


def _poisoned_scope(token: str, path: str, media_type: str) -> Scope:
    return cast(
        "Scope",
        {
            "type": "http",
            "asgi": {"spec_version": "2.0", "version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"origin", b"http://testserver"),
                (b"content-type", media_type.encode("ascii")),
                (b"content-length", _ABS_PATH.encode("ascii")),
                (b"x-csrf-token", token.encode("ascii")),
            ],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
        },
    )


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
