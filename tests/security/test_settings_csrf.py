"""Проверяет отказ CSRF и чужого Origin на сохранении настроек."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from tests.security._leaks import assert_no_secret_leak, leak_markers

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_KEY, _PROMPT, _ = leak_markers()


class _CsrfParser(HTMLParser):
    """Собирает скрытый CSRF-токен страницы настроек."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора."""
        super().__init__()
        self.token = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает значение поля csrf_token.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        if tag != "input" or attributes.get("name") != "csrf_token":
            return
        token = attributes.get("value")
        if token:
            self.token = token


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Собирает клиент с изолированным наложением и маркером ключа."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    return TestClient(create_app(skills_root=_SKILLS_ROOT))


@pytest.mark.parametrize(
    ("origin", "token_kind"),
    [
        ("https://attacker.example", "expected"),
        (None, "expected"),
        ("http://testserver", "wrong"),
        ("http://testserver", "missing"),
    ],
)
def test_settings_csrf_or_foreign_origin_returns_forbidden(
    client: TestClient,
    origin: str | None,
    token_kind: str,
) -> None:
    """Проверяет 403 settings_forbidden без секретов в отказе."""
    token = _csrf(client)
    submitted = {
        "expected": token,
        "wrong": "wrong-token",
        "missing": None,
    }[token_kind]
    headers = {"content-type": "application/json"}
    if origin is not None:
        headers["origin"] = origin
    body: dict[str, object] = {"values": {}}
    if submitted is not None:
        body["csrf_token"] = submitted

    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.post("/api/settings", json=body, headers=headers)

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "settings_forbidden",
            "message": "Изменение настроек запрещено.",
        }
    }
    if submitted is not None:
        assert submitted not in response.text
        assert submitted not in repr(logs)
    assert_no_secret_leak(response.text, logs)
    assert _PROMPT not in response.text
    assert _KEY not in response.text


def _csrf(client: TestClient) -> str:
    parser = _CsrfParser()
    parser.feed(client.get("/settings").text)
    assert parser.token
    return parser.token
