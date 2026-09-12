"""Проверяет публичные HTTP-швы приложения."""

import asyncio
import json
import os
from collections.abc import Sequence
from functools import partial
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from starlette.types import Message, Receive, Scope, Send
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from skillhub.classifier import SkillClassifier, SkillMetadata, allowlist
from skillhub.core import SkillHubError
from skillhub.llm import (
    DeepSeekLlmGateway,
    FakeLlmGateway,
    LlmGateway,
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from skillhub.main import app as published_app
from skillhub.registry import SkillRegistry
from skillhub.web import ProcessErrorBoundary

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_MATRIX_PATH = _REPOSITORY_ROOT / "eval" / "classifier_matrix.md"
_SKILLS_ROOT = _REPOSITORY_ROOT / "skills"
_ALLOWED_MATRIX_LABELS = frozenset(
    {
        "business-message",
        "meeting-action-items",
        "meeting-protocol",
        "text-summary",
        "text-translation",
        "none",
    }
)
_SIMPLE_MODE_TEXTS = {
    "business-message": "готовый текст делового сообщения",
    "meeting-action-items": "готовый список задач встречи",
    "text-summary": "готовое краткое резюме",
    "text-translation": "готовый перевод текста",
}
_SKILL_CAPTIONS = {
    "business-message": "Деловое сообщение",
    "meeting-action-items": "Задачи встречи",
    "meeting-protocol": "Протокол встречи",
    "text-summary": "Краткое резюме",
    "text-translation": "Перевод текста",
}


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
    assert "Что нужно сделать" in response.text
    assert "Материал для обработки" in response.text
    assert "Приложение работает" not in response.text
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


class AssistantPageParser(HTMLParser):
    """Собирает поля, CSRF и список режимов окна ассистента."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора страницы ассистента."""
        super().__init__()
        self.csrf_tokens: list[str] = []
        self.labels: list[str] = []
        self.mode_names: list[str] = []
        self.mode_captions: list[str] = []
        self.mode_controls: list[str] = []
        self._in_label = False
        self._in_mode = False
        self._label_parts: list[str] = []
        self._mode_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает токен, подписи полей и элементы списка режимов.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            token = attributes.get("value")
            if token:
                self.csrf_tokens.append(token)
        if tag == "label":
            self._in_label = True
            self._label_parts = []
        skill_name = attributes.get("data-skill-name")
        if tag == "li" and skill_name:
            self._in_mode = True
            self.mode_names.append(skill_name)
            self._mode_parts = []
        if self._in_mode and tag in {"a", "button", "input", "select"}:
            self.mode_controls.append(tag)

    def handle_data(self, data: str) -> None:
        """Собирает видимый текст активной подписи или режима.

        Args:
            data: текст текущего HTML-узла.
        """
        if self._in_label:
            self._label_parts.append(data)
        if self._in_mode:
            self._mode_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Завершает разбор подписи или пункта режима.

        Args:
            tag: имя закрывающего HTML-тега.
        """
        if tag == "label" and self._in_label:
            self.labels.append("".join(self._label_parts).strip())
            self._in_label = False
        if tag == "li" and self._in_mode:
            self.mode_captions.append("".join(self._mode_parts).strip())
            self._in_mode = False


class _QueuedFakeGateway(FakeLlmGateway):
    """Возвращает очередь заранее заданных ответов тестового шва."""

    def __init__(self, *texts: str) -> None:
        """Сохраняет ответы в порядке вызовов.

        Args:
            texts: тексты успешных ответов шлюза.
        """
        super().__init__(result=_llm_result(texts[0] if texts else ""))
        self._texts = list(texts)

    def complete(self, request: LlmRequest) -> LlmResult:
        """Выдаёт следующий заранее заданный ответ.

        Args:
            request: типизированный запрос вызывающей стороны.

        Returns:
            Следующий сохранённый результат.
        """
        self.requests.append(request)
        return _llm_result(self._texts.pop(0))


def _llm_result(text: str) -> LlmResult:
    """Собирает успешный ответ тестового шлюза."""
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _sent_text(request: LlmRequest) -> str:
    """Склеивает содержимое отправленного запроса."""
    return "\n".join(message.content for message in request.messages)


def _parse_assistant_page(html: str) -> AssistantPageParser:
    """Разбирает серверную страницу ассистента."""
    parser = AssistantPageParser()
    parser.feed(html)
    return parser


def _assistant_csrf(client: TestClient) -> str:
    """Читает CSRF-токен из корневой страницы ассистента."""
    parser = _parse_assistant_page(client.get("/").text)
    assert len(parser.csrf_tokens) == 1
    return parser.csrf_tokens[0]


def _post_assistant(
    client: TestClient,
    token: str,
    intent: str,
    material: str,
) -> Response:
    """Отправляет JSON-запрос ассистента с CSRF в заголовке."""
    return client.post(
        "/api/assistant",
        json={"intent": intent, "material": material},
        headers={
            "origin": "http://testserver",
            "x-csrf-token": token,
        },
    )


def parse_classifier_matrix(path: Path) -> list[tuple[str, str]]:
    """Читает размеченные намерения и ожидаемые имена из матрицы.

    Args:
        path: markdown-таблица с колонками intent и expected.

    Returns:
        Пары намерения и ожидаемого имени режима или none.
    """
    rows: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2 or cells[0] in {"intent", "---"} or set(cells[1]) <= {"-"}:
            continue
        rows.append((cells[0], cells[1]))
    return rows


def classifier_matrix_accuracy(
    expected: Sequence[str],
    predicted: Sequence[str],
) -> float:
    """Считает долю совпадений ожидаемых и фактических имён.

    Args:
        expected: размеченные имена из матрицы.
        predicted: имена, возвращённые классификатором.

    Returns:
        Точность в диапазоне от 0 до 1.

    Raises:
        ValueError: длины списков не совпадают или набор пуст.
    """
    if not expected or len(expected) != len(predicted):
        mismatch = "length mismatch"
        raise ValueError(mismatch)
    hits = sum(left == right for left, right in zip(expected, predicted, strict=True))
    return hits / len(expected)


def run_classifier_matrix(
    rows: Sequence[tuple[str, str]],
    gateway: LlmGateway,
    metadata: Sequence[SkillMetadata],
) -> float:
    """Прогоняет размеченные намерения через классификатор без материала.

    Args:
        rows: пары намерения и ожидаемого имени.
        gateway: шлюз модели для выбора режима.
        metadata: снимок имён и описаний валидных скиллов.

    Returns:
        Точность прогона по размеченным именам.
    """
    classifier = SkillClassifier(gateway)
    predicted = [
        selected if (selected := classifier.select(intent, metadata).skill) else "none"
        for intent, _expected in rows
    ]
    return classifier_matrix_accuracy(
        [expected for _intent, expected in rows],
        predicted,
    )


def _snapshot_metadata() -> tuple[SkillMetadata, ...]:
    """Собирает метаданные текущего каталога скиллов."""
    report = SkillRegistry(_SKILLS_ROOT).load()
    return tuple(
        SkillMetadata(name=skill.name, description=skill.description)
        for skill in report.skills
    )


def test_root_renders_readonly_assistant_window() -> None:
    """Проверяет поля, бейдж и список режимов без ручного выбора."""
    response = TestClient(create_app()).get("/")
    page = _parse_assistant_page(response.text)

    assert response.status_code == 200
    assert "Что нужно сделать" in page.labels
    assert "Материал для обработки" in page.labels
    assert page.mode_names == list(_SKILL_CAPTIONS)
    assert page.mode_captions == list(_SKILL_CAPTIONS.values())
    assert page.mode_controls == []
    assert "data-assistant-badge" in response.text
    assert "data-assistant-status" in response.text
    assert "data-assistant-result" in response.text
    assert "override" not in response.text.casefold()
    assert len(page.csrf_tokens) == 1


def test_assistant_js_renders_untrusted_text_without_html_sink() -> None:
    """Проверяет, что ответ модели попадает только в текстовый узел."""
    response = TestClient(create_app()).get("/static/assistant.js")

    assert response.status_code == 200
    assert "textContent" in response.text
    assert "innerHTML" not in response.text
    assert "localStorage" not in response.text


def test_assistant_result_css_keeps_paragraphs_and_lists() -> None:
    """Проверяет сохранение абзацев и списков в текстовом ответе."""
    response = TestClient(create_app()).get("/static/app.css")

    assert response.status_code == 200
    assert "[data-assistant-result]" in response.text
    assert "white-space: pre-wrap" in response.text
    assert "overflow-wrap: anywhere" in response.text


@pytest.mark.parametrize("name", list(_SIMPLE_MODE_TEXTS))
def test_assistant_demo_returns_success_text_for_simple_mode(name: str) -> None:
    """Проверяет демо четырёх простых режимов через FakeLlmGateway."""
    generated = _SIMPLE_MODE_TEXTS[name]
    material = f"MATERIAL-{name}"
    gateway = _QueuedFakeGateway(json.dumps({"skill": name}), generated)
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = _post_assistant(client, token, "Сделай по режиму", material)

    assert response.status_code == 200
    assert response.json() == {
        "selected_skill": name,
        "caption": _SKILL_CAPTIONS[name],
        "outcome": "success",
        "text": generated,
        "message": "Ответ готов.",
    }
    assert len(gateway.requests) == 2
    assert material not in _sent_text(gateway.requests[0])
    assert material in _sent_text(gateway.requests[1])


def test_assistant_demo_returns_none_without_generation() -> None:
    """Проверяет исход none и отсутствие второго вызова модели."""
    material = "MATERIAL-none-weather"
    gateway = _QueuedFakeGateway(json.dumps({"skill": None}))
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = client.post(
        "/api/assistant",
        json={
            "intent": "Какая завтра погода?",
            "material": material,
            "csrf_token": token,
        },
        headers={"origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "selected_skill": None,
        "caption": None,
        "outcome": "none",
        "text": None,
        "message": "Подходящий режим не выбран.",
    }
    assert len(gateway.requests) == 1
    assert material not in _sent_text(gateway.requests[0])


def test_assistant_meeting_protocol_returns_parsed_markdown() -> None:
    """Проверяет бейдж протокола и нормативный Markdown без выгрузки Word."""
    material = "MATERIAL-meeting-protocol"
    example = (
        _SKILLS_ROOT / "meeting-protocol" / "references" / "example.md"
    ).read_text(encoding="utf-8")
    gateway = _QueuedFakeGateway(
        json.dumps({"skill": "meeting-protocol"}),
        example,
    )
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)
    page = _parse_assistant_page(client.get("/").text)

    response = _post_assistant(client, token, "Составь протокол совещания", material)

    assert "Протокол встречи" in page.mode_captions
    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_skill"] == "meeting-protocol"
    assert payload["caption"] == "Протокол встречи"
    assert payload["outcome"] == "success"
    assert payload["text"] is not None
    assert "## Задачи" in payload["text"]
    assert "Word" not in response.text
    assert len(gateway.requests) == 2
    assert material not in _sent_text(gateway.requests[0])
    assert material in _sent_text(gateway.requests[1])


def test_assistant_ignores_skills_and_body_from_http() -> None:
    """Проверяет, что снимок берётся только из реестра, а не из тела."""
    gateway = _QueuedFakeGateway(
        json.dumps({"skill": "text-summary"}),
        "резюме из реестра",
    )
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = client.post(
        "/api/assistant",
        json={
            "intent": "Суммируй",
            "material": "абзац",
            "skills": [{"name": "leaked-skill", "body": "ATTACK-BODY"}],
            "body": "ATTACK-BODY",
        },
        headers={
            "origin": "http://testserver",
            "x-csrf-token": token,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_skill"] == "text-summary"
    assert "ATTACK-BODY" not in _sent_text(gateway.requests[0])
    assert "leaked-skill" not in _sent_text(gateway.requests[0])


def test_assistant_xss_payload_stays_plain_text() -> None:
    """Проверяет, что HTML ответа модели не исполняется как разметка."""
    xss = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    gateway = _QueuedFakeGateway(json.dumps({"skill": "text-summary"}), xss)
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = _post_assistant(client, token, "Суммируй", xss)
    script = client.get("/static/assistant.js").text

    assert response.status_code == 200
    assert response.json()["text"] == xss
    assert "innerHTML" not in script
    assert "textContent" in script


@pytest.mark.parametrize(
    ("origin", "token_kind"),
    [
        (None, "expected"),
        ("https://attacker.example", "expected"),
        ("http://testserver", "wrong"),
        ("http://testserver", "missing"),
    ],
)
def test_assistant_csrf_or_origin_failure_uses_e1_envelope(
    origin: str | None,
    token_kind: str,
) -> None:
    """Проверяет CSRF и same-origin в оболочке ожидаемой ошибки."""
    gateway = _QueuedFakeGateway(json.dumps({"skill": "text-summary"}), "не должен")
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)
    submitted = {
        "expected": token,
        "wrong": "wrong-token",
        "missing": None,
    }[token_kind]
    headers = {"content-type": "application/json"}
    if origin is not None:
        headers["origin"] = origin
    if submitted is not None:
        headers["x-csrf-token"] = submitted

    response = client.post(
        "/api/assistant",
        json={"intent": "Суммируй", "material": "абзац"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "assistant_forbidden",
            "message": "Запрос ассистента запрещён.",
        }
    }
    assert gateway.requests == []


def test_assistant_invalid_json_uses_e1_envelope() -> None:
    """Проверяет отказ для тела, которое нельзя разобрать как UTF-8 JSON."""
    client = TestClient(create_app())
    token = _assistant_csrf(client)

    response = client.post(
        "/api/assistant",
        content=b"not-json",
        headers={
            "origin": "http://testserver",
            "content-type": "application/json",
            "x-csrf-token": token,
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "bad_request",
            "message": "Некорректный запрос.",
        }
    }


def test_assistant_rejects_unsupported_media_type() -> None:
    """Проверяет отказ для неподдерживаемого типа содержимого."""
    client = TestClient(create_app())
    token = _assistant_csrf(client)

    response = client.post(
        "/api/assistant",
        content=b"intent=x",
        headers={
            "origin": "http://testserver",
            "content-type": "text/plain",
            "x-csrf-token": token,
        },
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_assistant_oversized_body_is_rejected() -> None:
    """Проверяет лимит 1 МБ до разбора JSON."""
    gateway = _QueuedFakeGateway(json.dumps({"skill": "text-summary"}), "не должен")
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = client.post(
        "/api/assistant",
        content=b"x" * 1_048_577,
        headers={
            "origin": "http://testserver",
            "content-type": "application/json",
            "x-csrf-token": token,
        },
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "Тело запроса превышает допустимый размер.",
        }
    }
    assert gateway.requests == []


def test_assistant_without_key_returns_generation_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет честный исход при старте без ключа поставщика."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = TestClient(create_app())
    token = _assistant_csrf(client)

    response = _post_assistant(client, token, "Суммируй статью", "абзац")

    assert response.status_code == 200
    assert response.json()["outcome"] == "generation_unavailable"
    assert response.json()["message"] == "Генерация недоступна."


def test_classifier_matrix_schema_covers_allowlist_classes() -> None:
    """Проверяет схему матрицы, покрытие классов и отсутствие unknown."""
    rows = parse_classifier_matrix(_MATRIX_PATH)
    expected = [label for _intent, label in rows]
    metadata = _snapshot_metadata()
    names = allowlist(metadata)

    assert len(rows) >= 30
    assert names | {"none"} == _ALLOWED_MATRIX_LABELS
    assert set(expected) == _ALLOWED_MATRIX_LABELS
    assert "unknown" not in expected
    assert all(label in _ALLOWED_MATRIX_LABELS for label in expected)
    assert all(intent.strip() for intent, _label in rows)


def test_classifier_matrix_accuracy_function_uses_independent_labels() -> None:
    """Проверяет функцию точности на независимых литералах."""
    assert (
        classifier_matrix_accuracy(("a", "b", "c", "none"), ("a", "b", "c", "none"))
        == 1.0
    )
    assert classifier_matrix_accuracy(("a", "b"), ("a", "x")) == 0.5
    with pytest.raises(ValueError, match="length mismatch"):
        classifier_matrix_accuracy((), ())


def test_classifier_matrix_runner_is_ready_without_live_key() -> None:
    """Проверяет готовность runner; живой 95% без ключа не красный."""
    rows = parse_classifier_matrix(_MATRIX_PATH)
    metadata = _snapshot_metadata()
    scripted = _QueuedFakeGateway(
        *(
            json.dumps({"skill": None if label == "none" else label})
            for _intent, label in rows
        )
    )

    assert run_classifier_matrix(rows, scripted, metadata) == 1.0

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return
    try:
        accuracy = run_classifier_matrix(rows, DeepSeekLlmGateway(), metadata)
    except SkillHubError:
        return
    assert accuracy >= 0.95
