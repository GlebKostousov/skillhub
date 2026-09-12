"""Проверяет страницу настроек, JSON-снимок и полную замену наложения."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from skillhub.app_factory import create_app
from skillhub.runtime._constants import EDITABLE_FIELDS, FIELD_HINTS

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
_ORIGIN = {"origin": "http://testserver"}
_CREDENTIAL_MARKERS = (
    "DEEPSEEK_API_KEY",
    "deepseek_api_key",
    "provider_credential",
    "api_key",
    "ключ задан",
    "key is set",
)


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
    """Собирает клиент с изолированным файлом наложения."""
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app(skills_root=_SKILLS_ROOT))


def test_settings_page_and_json_share_catalog(client: TestClient) -> None:
    """Проверяет HTML и JSON из одного снимка с подсказками и границами."""
    page = client.get("/settings")
    payload = client.get("/api/settings").json()
    names = [field["name"] for field in payload["fields"]]
    by_name = {field["name"]: field for field in payload["fields"]}

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "<h1" in page.text
    assert "Настройки" in page.text
    assert 'aria-current="page"' in page.text
    assert '<a href="/settings"' in page.text
    assert names == list(EDITABLE_FIELDS)
    assert "environment" not in names
    assert "usage_path" not in names
    for name, hint in FIELD_HINTS.items():
        assert hint in page.text
        assert by_name[name]["hint"] == hint
        assert by_name[name]["kind"]
        assert "default" in by_name[name]
        assert hint in by_name[name]["hint"]
    assert "По умолчанию" in page.text
    assert "Диапазон" in page.text or "Допустимо" in page.text
    assert "environment" in page.text
    assert "usage_path" in page.text
    assert payload == client.get("/api/settings").json()


def test_settings_json_omits_provider_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отсутствие ключа поставщика и флага «ключ задан»."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-settings-must-not-leak")
    payload = client.get("/api/settings").json()
    page = client.get("/settings")
    observed = page.text + str(payload)

    assert set(payload) == {"fields"}
    for marker in _CREDENTIAL_MARKERS:
        assert marker.casefold() not in observed.casefold()
    assert "sk-settings-must-not-leak" not in observed


def test_settings_post_then_get_reflects_store(client: TestClient) -> None:
    """Проверяет полную замену и отражение в GET и RuntimeStore."""
    token = _csrf(client)
    before = client.get("/api/settings").json()
    values = _values_from(before)
    ceiling = next(
        field["max"] for field in before["fields"] if field["name"] == "max_tokens"
    )
    values["max_tokens"] = ceiling
    values["temperature"] = 0.5
    values["stream"] = False

    response = _post_settings(client, token, values)
    after = client.get("/api/settings").json()
    page = client.get("/settings")
    store = client.app.state.runtime_store

    assert response.status_code == 200
    assert response.json() == after
    assert _values_from(after)["temperature"] == 0.5
    assert _values_from(after)["max_tokens"] == ceiling
    assert store.snapshot().values.temperature == 0.5
    assert store.snapshot().values.max_tokens == ceiling
    assert "0.5" in page.text


def test_settings_unknown_key_is_invalid_overlay(client: TestClient) -> None:
    """Проверяет отказ для лишнего ключа в полном наборе значений."""
    token = _csrf(client)
    before = _values_from(client.get("/api/settings").json())
    values = dict(before)
    values["unexpected"] = True

    response = _post_settings(client, token, values)

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_overlay",
            "message": "Наложение настроек отклонено.",
        }
    }
    assert _values_from(client.get("/api/settings").json()) == before
    assert "unexpected" not in before


def test_settings_js_uses_text_content_only() -> None:
    """Проверяет, что сценарий страницы пишет только текстовые узлы."""
    script = TestClient(create_app()).get("/static/settings.js")

    assert script.status_code == 200
    assert "textContent" in script.text
    assert "innerHTML" not in script.text


def _csrf(client: TestClient) -> str:
    parser = _CsrfParser()
    parser.feed(client.get("/settings").text)
    assert parser.token
    return parser.token


def _values_from(payload: dict[str, object]) -> dict[str, object]:
    fields = payload["fields"]
    assert isinstance(fields, list)
    return {field["name"]: field["value"] for field in fields}


def _post_settings(
    client: TestClient,
    token: str,
    values: dict[str, object],
) -> Response:
    return client.post(
        "/api/settings",
        json={"csrf_token": token, "values": values},
        headers=_ORIGIN,
    )
