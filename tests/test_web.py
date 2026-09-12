"""Проверяет публичные HTTP-швы приложения."""

import asyncio
from functools import partial
from html.parser import HTMLParser
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from skillhub.core import SkillHubError
from skillhub.main import app as published_app
from skillhub.web import ProcessErrorBoundary


class ExpectedFailureError(SkillHubError):
    """Ожидаемый отказ тестового HTTP-шва."""

    code = "expected_failure"
    status_code = 422
    public_message = "Запрос отклонён."


class RootAccessibilityParser(HTMLParser):
    """Собирает доступные переходы и их фокусируемые цели."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора корневой страницы."""
        super().__init__()
        self.skip_links: list[tuple[str, set[str], str]] = []
        self.focusable_targets: set[str] = set()
        self._active_skip_link: tuple[str, set[str]] | None = None
        self._active_label: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает skip-link и фокусируемую цель.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id and attributes.get("tabindex") == "-1":
            self.focusable_targets.add(element_id)
        classes = set((attributes.get("class") or "").split())
        href = attributes.get("href")
        if (
            tag == "a"
            and isinstance(href, str)
            and href.startswith("#")
            and "visually-hidden-focusable" in classes
        ):
            self._active_skip_link = (href, classes)
            self._active_label = []

    def handle_data(self, data: str) -> None:
        """Собирает видимую подпись активной ссылки.

        Args:
            data: текст текущего HTML-узла.
        """
        if self._active_skip_link is not None:
            self._active_label.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Завершает разбор активной ссылки.

        Args:
            tag: имя закрывающего HTML-тега.
        """
        if tag != "a" or self._active_skip_link is None:
            return
        href, classes = self._active_skip_link
        self.skip_links.append((href, classes, "".join(self._active_label).strip()))
        self._active_skip_link = None
        self._active_label = []


async def _fail_after_response_start(
    private_input: str,
    _scope: Scope,
    _receive: Receive,
    send: Send,
) -> None:
    """Поднимает ошибку после отправки начала ответа.

    Args:
        private_input: закрытая строка для проверки безопасной ошибки.
        send: callback отправки ASGI-сообщения.

    Raises:
        RuntimeError: всегда после первого ASGI-сообщения.
    """
    await send({"type": "http.response.start", "status": 200, "headers": []})
    raise RuntimeError(private_input)


async def _receive_disconnect() -> Message:
    """Возвращает отключение тестового HTTP-клиента."""
    return {"type": "http.disconnect"}


async def _capture_message(messages: list[Message], message: Message) -> None:
    """Сохраняет отправленное ASGI-сообщение.

    Args:
        messages: коллекция отправленных сообщений.
        message: сообщение проверяемого приложения.
    """
    messages.append(message)


def test_root_returns_accessible_server_rendered_page() -> None:
    """Проверяет доступность минимальной серверной страницы."""
    response = TestClient(create_app()).get("/")
    accessibility = RootAccessibilityParser()
    accessibility.feed(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="ru">' in response.text
    assert "<main" in response.text
    assert '<main id="content" tabindex="-1"' in response.text
    assert accessibility.skip_links == [
        ("#content", {"visually-hidden-focusable"}, "К содержимому")  # noqa: RUF001
    ]
    assert "content" in accessibility.focusable_targets
    assert "<h1>SkillHub</h1>" in response.text
    assert response.text.count("Приложение работает") == 1
    assert "Сервис" not in response.text
    assert "https://" not in response.text


def test_health_returns_stable_success_contract() -> None:
    """Проверяет стабильный ответ о готовности приложения."""
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_repeated_apps_and_health_requests_remain_independent() -> None:
    """Проверяет повтор без общего изменяемого состояния."""
    first_client = TestClient(create_app())
    second_client = TestClient(create_app())

    responses = [
        first_client.get("/health"),
        first_client.get("/health"),
        second_client.get("/health"),
    ]

    assert [response.json() for response in responses] == [
        {"status": "ok"},
        {"status": "ok"},
        {"status": "ok"},
    ]
    request_ids = [response.headers["x-request-id"] for response in responses]
    assert len(set(request_ids)) == len(request_ids)


def test_expected_error_returns_and_logs_only_safe_fields() -> None:
    """Проверяет безопасное отображение ожидаемой ошибки."""
    app = create_app()

    @app.get("/expected-error")
    def expected_error() -> None:
        """Поднимает ожидаемую ошибку для проверки публичного шва.

        Raises:
            ExpectedFailureError: всегда при вызове тестового маршрута.
        """
        raise ExpectedFailureError

    private_input = "user-content-secret-42"
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = TestClient(app, raise_server_exceptions=False).get(
            "/expected-error",
            params={"material": private_input},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "expected_failure",
            "message": "Запрос отклонён.",
        }
    }
    assert private_input not in response.text
    assert private_input not in repr(logs)
    assert "traceback" not in response.text.lower()
    assert logs[0]["request_id"] == response.headers["x-request-id"]


def test_security_baseline_preserves_origin_for_same_origin_reload_form() -> None:
    """Сохраняет Origin обычной формы для строгой проверки перезагрузки."""
    response = TestClient(create_app()).get("/")

    assert response.headers["content-security-policy"] == (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert len(response.headers["x-request-id"]) == 32


def test_factory_application_owns_safe_unexpected_error_boundary() -> None:
    """Проверяет полный 500-контракт собранного приложения."""
    app = create_app()
    private_input = "private-runtime-content-84"

    @app.get("/unexpected-error")
    def unexpected_error() -> None:
        """Поднимает неожиданную ошибку для проверки публичного шва.

        Raises:
            RuntimeError: всегда при вызове тестового маршрута.
        """
        raise RuntimeError(private_input)

    with capture_logs() as logs:
        response = TestClient(app).get("/unexpected-error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Внутренняя ошибка сервера.",
        }
    }
    assert private_input not in response.text
    assert private_input not in repr(logs)
    assert "traceback" not in repr(logs).lower()
    assert logs[0]["request_id"] == response.headers["x-request-id"]
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )


def test_process_boundary_builds_safe_fallback_before_response() -> None:
    """Проверяет безопасный резервный ответ до начала передачи."""
    private_input = "private-boundary-content-3481"

    async def fail_before_response(
        _scope: Scope,
        _receive: Receive,
        _send: Send,
    ) -> None:
        """Поднимает ошибку до отправки ASGI-ответа.

        Raises:
            RuntimeError: всегда при вызове тестового приложения.
        """
        raise RuntimeError(private_input)

    with capture_logs() as logs:
        response = TestClient(ProcessErrorBoundary(fail_before_response)).get("/")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Внутренняя ошибка сервера.",
        }
    }
    assert private_input not in repr(logs)
    assert logs[0]["request_id"] == response.headers["x-request-id"]


def test_process_boundary_aborts_safely_after_response_start() -> None:
    """Проверяет безопасный обрыв после начала HTTP-ответа."""
    private_input = "private-late-boundary-content-9214"
    request_id = "0123456789abcdef0123456789abcdef"
    messages: list[Message] = []

    scope = cast(
        "Scope",
        {
            "type": "http",
            "method": "GET",
            "path": "/late-error",
            "raw_path": b"/late-error",
            "query_string": b"",
            "headers": [],
            "state": {"request_id": request_id},
        },
    )
    boundary = ProcessErrorBoundary(partial(_fail_after_response_start, private_input))

    with (
        capture_logs() as logs,
        pytest.raises(
            RuntimeError,
            match=r"Передача HTTP-ответа прервана\.",
        ) as captured,
    ):
        asyncio.run(
            boundary(
                scope,
                _receive_disconnect,
                partial(_capture_message, messages),
            )
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert private_input not in repr(captured.value)
    assert messages == [{"type": "http.response.start", "status": 200, "headers": []}]
    assert len(logs) == 1
    assert logs[0]["event"] == "http.response_aborted"
    assert logs[0]["error_code"] == "response_aborted"
    assert logs[0]["error_type"] == "RuntimeError"
    assert logs[0]["request_id"] == request_id
    assert private_input not in repr(logs)


def test_untrusted_host_is_rejected() -> None:
    """Проверяет отказ для внешнего имени хоста."""
    response = TestClient(create_app()).get(
        "/",
        headers={"host": "attacker.example"},
    )

    assert response.status_code == 400
    assert "attacker.example" not in response.text
    assert "traceback" not in response.text.lower()
    assert "SkillHub" not in response.text


def test_main_publishes_factory_application() -> None:
    """Проверяет штатную точку входа процесса."""
    response = TestClient(published_app).get("/health")

    assert response.json() == {"status": "ok"}


def test_bootstrap_is_served_from_pinned_local_asset() -> None:
    """Проверяет локальную поставку зафиксированного Bootstrap."""
    response = TestClient(create_app()).get("/static/vendor/bootstrap-5.3.8.min.css")

    assert response.status_code == 200
    assert "v5.3.8 (https://getbootstrap.com/)" in response.text


def test_unknown_route_does_not_disclose_internals_or_enable_cors() -> None:
    """Проверяет закрытый ответ для неизвестного маршрута."""
    private_marker = "private-missing-path-21"
    response = TestClient(create_app()).get(
        f"/{private_marker}",
        headers={"origin": "https://attacker.example"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert private_marker not in response.text
    assert "traceback" not in response.text.lower()
    assert "access-control-allow-origin" not in response.headers


def test_unsupported_method_uses_safe_envelope_without_cors() -> None:
    """Проверяет единый отказ для метода и выключенный CORS."""
    response = TestClient(create_app()).post(
        "/health",
        headers={
            "origin": "https://attacker.example",
            "access-control-request-method": "POST",
        },
    )

    assert response.status_code == 405
    assert response.headers["content-type"].startswith("application/json")
    assert "/health" not in response.text
    assert "traceback" not in response.text.lower()
    assert not any(header.startswith("access-control-") for header in response.headers)
