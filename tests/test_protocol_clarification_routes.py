"""Проверяет HTTP-швы таблицы уточнений на странице протокола."""

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from httpx2 import Response
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from skillhub.protocol import parse

_WEB_ROOT = Path(__file__).resolve().parents[1] / "src" / "skillhub" / "web"
_PROTOCOL_HTML = _WEB_ROOT / "templates" / "protocol.html"
_PROTOCOL_JS = _WEB_ROOT / "static" / "protocol.js"
_ORIGIN = {"origin": "http://testserver"}
_VALID_MARKDOWN = """# Протокол встречи: Тема

**Дата:** 2026-09-12
**Участники:** Анна

## Обсуждение
- пункт

## Решения
- решение

## Задачи
- —

## Открытые вопросы
- вопрос
"""
_GAP_MARKDOWN = _VALID_MARKDOWN.replace("**Дата:** 2026-09-12", "**Дата:** —")
_INVALID_MARKDOWN = "это не протокол"
_DATE_QUESTION = {
    "id": "date",
    "target": "date",
    "reason": "Дата встречи не указана.",
    "hint": "Укажите дату встречи.",
    "status": "pending",
}


class _CsrfParser(HTMLParser):
    """Собирает скрытые CSRF-токены страницы протокола."""

    def __init__(self) -> None:
        """Создаёт пустой список найденных токенов."""
        super().__init__()
        self.tokens: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает значение скрытого поля CSRF.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            token = attributes.get("value")
            if token:
                self.tokens.append(token)


def test_protocol_page_renders_clarification_table_without_generator() -> None:
    """Проверяет таблицу уточнений на странице без порта генерации."""
    response = TestClient(create_app()).get("/protocol")
    html = _PROTOCOL_HTML.read_text(encoding="utf-8")
    script = _PROTOCOL_JS.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "Генерация черновика недоступна." in response.text
    assert "Проблема" in response.text
    assert "Ответ" in response.text
    assert "Действия" in response.text
    assert "Статус" in response.text
    assert 'id="protocol-clarifications-table"' in response.text
    assert 'id="protocol-markdown"' in html
    assert "Отправить" in script
    assert "Пропустить" in script
    assert "Отменить выбор" in script
    assert "/protocol/clarifications" in script
    assert "/protocol/answer" in script
    assert "innerHTML" not in script
    assert "trim" in script


def test_clarifications_return_pending_questions_from_markdown() -> None:
    """Проверяет список вопросов из Markdown без вызова генератора."""
    client = TestClient(create_app())
    response = _post_json(
        client,
        "/protocol/clarifications",
        _csrf(client),
        {"text": _GAP_MARKDOWN},
    )

    assert response.status_code == 200
    assert response.json() == {"clarifications": [_DATE_QUESTION]}


def test_clarifications_parse_error_keeps_draft_envelope() -> None:
    """Проверяет ту же оболочку разбора, что и у черновика."""
    client = TestClient(create_app())
    with capture_logs() as logs:
        response = _post_json(
            client,
            "/protocol/clarifications",
            _csrf(client),
            {"text": _INVALID_MARKDOWN},
        )

    error = response.json()["error"]
    assert response.status_code == 422
    assert set(error) == {"code", "message", "line", "expected", "got"}
    assert error["code"] == "protocol_parse_error"
    assert error["message"] == "Структура протокола нарушена."
    assert isinstance(error["line"], int)
    assert isinstance(error["expected"], str)
    assert error["got"] == _INVALID_MARKDOWN
    assert _INVALID_MARKDOWN not in repr(logs)


def test_answer_applies_one_decision_and_returns_text() -> None:
    """Проверяет применение одного ответа и обновлённый Markdown."""
    client = TestClient(create_app())
    response = _post_json(
        client,
        "/protocol/answer",
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "id": "date",
            "action": "answer",
            "value": "2026-10-01",
        },
    )

    payload = response.json()
    protocol = parse(payload["text"])
    assert response.status_code == 200
    assert protocol.date == "2026-10-01"
    assert protocol.participants == ("Анна",)
    assert payload["clarifications"] == []


def test_empty_answer_is_rejected_by_server() -> None:
    """Проверяет серверный отказ пустого ответа без доверия к браузеру."""
    client = TestClient(create_app())
    token = _csrf(client)
    empty = _post_json(
        client,
        "/protocol/answer",
        token,
        {"text": _GAP_MARKDOWN, "id": "date", "action": "answer", "value": ""},
    )
    missing = _post_json(
        client,
        "/protocol/answer",
        token,
        {"text": _GAP_MARKDOWN, "id": "date", "action": "answer"},
    )
    spaces = _post_json(
        client,
        "/protocol/answer",
        token,
        {"text": _GAP_MARKDOWN, "id": "date", "action": "answer", "value": "   "},
    )

    for response in (empty, missing, spaces):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "empty_clarification_answer"


def test_skip_does_not_require_value_and_keeps_placeholder() -> None:
    """Проверяет пропуск без текста и сохранение заполнителя."""
    client = TestClient(create_app())
    response = _post_json(
        client,
        "/protocol/answer",
        _csrf(client),
        {"text": _GAP_MARKDOWN, "id": "date", "action": "skip"},
    )

    payload = response.json()
    assert response.status_code == 200
    assert parse(payload["text"]).date == "—"
    assert payload["clarifications"] == [_DATE_QUESTION]


def test_foreign_clarification_id_is_rejected() -> None:
    """Проверяет отказ чужого идентификатора уточнения."""
    client = TestClient(create_app())
    response = _post_json(
        client,
        "/protocol/answer",
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "id": "title",
            "action": "answer",
            "value": "подмена",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_clarification_id"
    assert "title" not in response.text
    assert "подмена" not in response.text


def test_clarification_routes_reject_foreign_origin_and_wrong_csrf() -> None:
    """Проверяет отказ уточнений при чужом Origin и неверном CSRF."""
    client = TestClient(create_app())
    token = _csrf(client)
    foreign = client.post(
        "/protocol/clarifications",
        json={"csrf_token": token, "text": _GAP_MARKDOWN},
        headers={"origin": "https://attacker.example"},
    )
    wrong = _post_json(
        client,
        "/protocol/answer",
        "wrong-csrf-token-value",
        {"text": _GAP_MARKDOWN, "id": "date", "action": "skip"},
    )

    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "protocol_forbidden"
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "protocol_forbidden"


def test_answer_parse_error_keeps_draft_envelope() -> None:
    """Проверяет оболочку разбора на шве одного ответа."""
    client = TestClient(create_app())
    response = _post_json(
        client,
        "/protocol/answer",
        _csrf(client),
        {
            "text": _INVALID_MARKDOWN,
            "id": "date",
            "action": "skip",
        },
    )

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "protocol_parse_error"
    assert set(error) == {"code", "message", "line", "expected", "got"}


def _csrf(client: TestClient) -> str:
    """Читает CSRF-токен со страницы протокола.

    Args:
        client: клиент одного экземпляра приложения.
    """
    parser = _CsrfParser()
    parser.feed(client.get("/protocol").text)
    assert len(parser.tokens) == 1
    return parser.tokens[0]


def _post_json(
    client: TestClient,
    path: str,
    token: str,
    fields: dict[str, str],
) -> Response:
    """Отправляет JSON-запрос протокола с Origin и секретом.

    Args:
        client: HTTP-клиент проверяемого приложения.
        path: путь изменяющего маршрута.
        token: ожидаемый секрет CSRF.
        fields: прикладные поля тела запроса.
    """
    return client.post(
        path,
        json={"csrf_token": token, **fields},
        headers=_ORIGIN,
    )
