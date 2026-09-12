"""Дополняет gate E1-S4 риск-ориентированными HTTP и UI-проверками."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

import pytest
import yaml
from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.types import Message, Scope
from structlog.testing import capture_logs

from skillhub.app_factory import create_app
from skillhub.registry import LoadIssue, LoadReport, SkillRegistry

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = _REPOSITORY_ROOT / "skills"
_EXPECTED_NAMES = [
    "business-message",
    "meeting-action-items",
    "meeting-protocol",
    "text-summary",
    "text-translation",
]
_SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
}
_ERROR_MESSAGES = {
    "reload_forbidden": "Перезагрузка каталога запрещена.",
    "request_too_large": "Тело запроса превышает допустимый размер.",
    "unsupported_media_type": "Формат тела запроса не поддерживается.",
}


class _HttpResponse(Protocol):
    """Описывает минимальную поверхность HTTP-ответа для общего стража."""

    @property
    def headers(self) -> Mapping[str, str]:
        """Возвращает заголовки ответа."""
        ...


@dataclass(frozen=True, slots=True)
class _RejectedReloadCase:
    """Описывает один отказ изменяющего запроса."""

    name: str
    origins: tuple[str, ...]
    media_type: str
    body_kind: str
    status: int
    code: str


class _CatalogPageParser(HTMLParser):
    """Собирает публичные карточки, формы и локальные ресурсы каталога."""

    def __init__(self) -> None:
        """Создаёт пустой структурный результат."""
        super().__init__()
        self.asset_urls: list[str] = []
        self.card_names: list[str] = []
        self.csrf_tokens: list[str] = []
        self.dangerous_nodes: list[str] = []
        self.query_values: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает только проверяемые атрибуты открывающего тега.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: разобранные атрибуты тега.
        """
        attributes = dict(attrs)
        self._remember_card(tag, attributes)
        self._remember_input(tag, attributes)
        self._remember_asset(tag, attributes)
        self._remember_dangerous_node(tag, attributes)

    def _remember_card(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag == "article" and (name := attributes.get("data-skill-name")):
            self.card_names.append(name)

    def _remember_input(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag != "input":
            return
        value = attributes.get("value")
        if attributes.get("name") == "csrf_token" and value:
            self.csrf_tokens.append(value)
        if attributes.get("name") == "q" and value is not None:
            self.query_values.append(value)

    def _remember_asset(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag == "script":
            source = attributes.get("src")
            if source is None:
                self.dangerous_nodes.append("inline-script")
            else:
                self.asset_urls.append(source)
        if tag == "link" and (href := attributes.get("href")):
            self.asset_urls.append(href)

    def _remember_dangerous_node(
        self,
        tag: str,
        attributes: dict[str, str | None],
    ) -> None:
        if tag in {"iframe", "object"}:
            self.dangerous_nodes.append(tag)
        if any(name.casefold().startswith("on") for name in attributes):
            self.dangerous_nodes.append(f"{tag}-event-handler")


def _parse_page(text: str) -> _CatalogPageParser:
    parser = _CatalogPageParser()
    parser.feed(text)
    return parser


def _write_valid_skill(root: Path, name: str, *, caption: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    metadata = yaml.safe_dump(
        {
            "name": name,
            "caption": caption,
            "description": f"Описание {name}",
        },
        allow_unicode=True,
        sort_keys=False,
    )
    (directory / "SKILL.md").write_text(
        f"---\n{metadata}---\nPrivate body {name}.",
        encoding="utf-8",
    )


def _csrf_token(client: TestClient) -> str:
    tokens = _parse_page(client.get("/skills").text).csrf_tokens
    assert len(tokens) == 1
    return tokens[0]


def _assert_security_headers(response: _HttpResponse) -> None:
    headers = response.headers
    for name, expected in _SECURITY_HEADERS.items():
        assert headers[name] == expected
    request_id = headers["x-request-id"]
    assert len(request_id) == 32
    int(request_id, 16)
    assert not any(name.startswith("access-control-") for name in headers)


def test_factory_performs_exactly_one_startup_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фиксирует загрузку снимка при сборке, а не при первом запросе."""
    calls: list[SkillRegistry] = []
    original_reload = SkillRegistry.reload

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        calls.append(registry)
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)

    app = create_app(skills_root=_SKILLS_ROOT)

    assert len(calls) == 1
    assert list(calls[0].snapshot) == _EXPECTED_NAMES
    assert not hasattr(app.state, "skill_registry")
    assert [item["name"] for item in TestClient(app).get("/api/skills").json()] == (
        _EXPECTED_NAMES
    )


def test_api_and_html_have_exact_field_parity_order_and_no_body() -> None:
    """Сверяет все публичные поля карточек с JSON, включая has_files."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app)

    api_response = client.get("/api/skills", params={"q": ""})
    page_response = client.get("/skills", params={"q": ""})
    payload = api_response.json()
    parser = _parse_page(page_response.text)

    assert [item["name"] for item in payload] == _EXPECTED_NAMES
    assert parser.card_names == _EXPECTED_NAMES
    assert all(
        set(item) == {"name", "caption", "description", "has_files"} for item in payload
    )
    card_starts = [
        page_response.text.index(f'data-skill-name="{item["name"]}"')
        for item in payload
    ]
    assert card_starts == sorted(card_starts)
    for index, (item, start) in enumerate(zip(payload, card_starts, strict=True)):
        end = (
            card_starts[index + 1]
            if index + 1 < len(card_starts)
            else page_response.text.index("</section>", start)
        )
        card = page_response.text[start:end]
        assert item["caption"] in card
        assert item["description"] in card
        expected_files_text = (
            "Есть дополнительные файлы"
            if item["has_files"]
            else "Дополнительных файлов нет"
        )
        assert expected_files_text in card
    for skill in SkillRegistry(_SKILLS_ROOT).load().skills:
        assert skill.body not in api_response.text
        assert skill.body not in page_response.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("STRASSE", ["unicode-caption"]),
        ("UNICODE-CAPTION", ["unicode-caption"]),
    ],
)
def test_http_search_uses_casefold_for_caption_and_name(
    tmp_path: Path,
    query: str,
    expected: list[str],
) -> None:
    """Проверяет Unicode casefold на обоих HTTP-представлениях."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "unicode-caption", caption="Straße")
    _write_valid_skill(root, "unrelated", caption="Другой режим")
    client = TestClient(create_app(skills_root=root))

    api_response = client.get("/api/skills", params={"q": query})
    page_response = client.get("/skills", params={"q": query})

    assert [item["name"] for item in api_response.json()] == expected
    assert _parse_page(page_response.text).card_names == expected


def test_query_length_boundary_is_shared_and_non_mutating() -> None:
    """Убивает off-by-one между допустимыми 128 и запрещёнными 129 знаками."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app)
    before = client.get("/api/skills").json()

    accepted_api = client.get("/api/skills", params={"q": "я" * 128})
    accepted_page = client.get("/skills", params={"q": "я" * 128})
    rejected_api = client.get("/api/skills", params={"q": "я" * 129})
    rejected_page = client.get("/skills", params={"q": "я" * 129})

    assert accepted_api.status_code == accepted_page.status_code == 200
    assert accepted_api.json() == []
    assert _parse_page(accepted_page.text).card_names == []
    assert rejected_api.status_code == rejected_page.status_code == 422
    assert rejected_api.json()["error"]["code"] == "query_too_long"
    assert client.get("/api/skills").json() == before


def test_hostile_query_is_escaped_in_attribute_context() -> None:
    """Проверяет экранирование отражённого q без новых тегов и обработчиков."""
    query = '"><script>alert("query-xss")</script><input onfocus="x">'
    response = TestClient(create_app(skills_root=_SKILLS_ROOT)).get(
        "/skills",
        params={"q": query},
    )
    parser = _parse_page(response.text)

    assert response.status_code == 200
    assert parser.query_values == [query]
    assert "<script>alert" not in response.text
    assert "<input onfocus" not in response.text
    assert parser.dangerous_nodes == []


def test_default_reload_reports_five_zero_and_is_idempotent() -> None:
    """Проверяет штатные 5/0 и отсутствие дублей после повторного POST."""
    client = TestClient(create_app(skills_root=_SKILLS_ROOT))
    token = _csrf_token(client)

    responses = [
        client.post(
            "/skills/reload",
            data={"csrf_token": token},
            headers={"origin": "http://testserver"},
        )
        for _ in range(2)
    ]

    for response in responses:
        assert response.status_code == 200
        assert "Загружено: 5" in response.text
        assert "Пропущено: 0" in response.text
        assert _parse_page(response.text).card_names == _EXPECTED_NAMES
    assert [
        item["name"] for item in client.get("/api/skills").json()
    ] == _EXPECTED_NAMES


def test_existing_empty_root_reloads_as_zero_zero(tmp_path: Path) -> None:
    """Отличает пустой каталог 0/0 от отсутствующего корня 0/1."""
    root = tmp_path / "skills"
    root.mkdir()
    client = TestClient(create_app(skills_root=root))

    initial = client.get("/skills")
    reloaded = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
    )

    assert initial.status_code == reloaded.status_code == 200
    assert "Каталог пока пуст." in initial.text
    assert "root_missing" not in initial.text
    assert "Загружено: 0" in reloaded.text
    assert "Пропущено: 0" in reloaded.text
    assert client.get("/api/skills").json() == []


@pytest.mark.parametrize(
    "scenario",
    [
        _RejectedReloadCase(
            "missing-origin",
            (),
            "form",
            "expected",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "null-origin",
            ("null",),
            "form",
            "expected",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "foreign-origin",
            ("https://attacker.example",),
            "form",
            "expected",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "duplicate-origin",
            ("http://testserver", "https://attacker.example"),
            "form",
            "expected",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "missing-token",
            ("http://testserver",),
            "form",
            "missing",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "wrong-token",
            ("http://testserver",),
            "form",
            "wrong",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "duplicate-token",
            ("http://testserver",),
            "form",
            "duplicate",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "extra-field",
            ("http://testserver",),
            "form",
            "extra",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "invalid-utf8",
            ("http://testserver",),
            "form",
            "invalid-utf8",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "unicode-token",
            ("http://testserver",),
            "form",
            "unicode-token",
            403,
            "reload_forbidden",
        ),
        _RejectedReloadCase(
            "json-body",
            ("http://testserver",),
            "json",
            "expected",
            415,
            "unsupported_media_type",
        ),
        _RejectedReloadCase(
            "oversized-body",
            ("http://testserver",),
            "form",
            "oversized",
            413,
            "request_too_large",
        ),
    ],
    ids=lambda scenario: scenario.name,
)
def test_rejected_reload_matrix_never_publishes_snapshot(
    tmp_path: Path,
    scenario: _RejectedReloadCase,
) -> None:
    """Проверяет Origin/CSRF/body matrix и ссылочную неизменность снимка."""
    root = tmp_path / scenario.name
    _write_valid_skill(root, "alpha", caption="Альфа")
    app = create_app(skills_root=root)
    client = TestClient(app)
    token = _csrf_token(client)
    _write_valid_skill(root, "bravo", caption="Браво")
    private_marker = "private-rejected-body-721"
    bodies = {
        "expected": f"csrf_token={token}".encode(),
        "missing": f"material={private_marker}".encode(),
        "wrong": f"csrf_token={private_marker}".encode(),
        "duplicate": f"csrf_token={token}&csrf_token={token}".encode(),
        "extra": f"csrf_token={token}&material={private_marker}".encode(),
        "invalid-utf8": b"csrf_token=\xff",
        "unicode-token": b"csrf_token=%D1%8F",
        "oversized": private_marker.encode() + b"x" * 4_096,
    }
    headers = [("origin", origin) for origin in scenario.origins]
    headers.append(
        (
            "content-type",
            "application/json"
            if scenario.media_type == "json"
            else "application/x-www-form-urlencoded",
        )
    )

    with capture_logs() as logs:
        response = client.post(
            "/skills/reload",
            content=bodies[scenario.body_kind],
            headers=headers,
        )

    assert response.status_code == scenario.status
    assert response.json() == {
        "error": {
            "code": scenario.code,
            "message": _ERROR_MESSAGES[scenario.code],
        }
    }
    assert [item["name"] for item in client.get("/api/skills").json()] == ["alpha"]
    assert private_marker not in response.text
    assert private_marker not in repr(logs)
    assert token not in repr(logs)
    _assert_security_headers(response)


def test_cors_preflights_are_rejected_on_catalog_and_reload() -> None:
    """Проверяет отсутствие запасного cross-origin пути на новых маршрутах."""
    client = TestClient(create_app(skills_root=_SKILLS_ROOT))
    responses = [
        client.options(
            path,
            headers={
                "origin": "https://attacker.example",
                "access-control-request-method": method,
            },
        )
        for path, method in [
            ("/api/skills", "GET"),
            ("/skills", "GET"),
            ("/skills/reload", "POST"),
        ]
    ]

    for response in responses:
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"
        _assert_security_headers(response)


def test_untrusted_host_error_has_safe_envelope_and_headers() -> None:
    """Проверяет полный Host/CORS/header контракт отказа."""
    private_host = "private-host.attacker.example"
    response = TestClient(create_app(skills_root=_SKILLS_ROOT)).get(
        "/skills",
        headers={
            "host": private_host,
            "origin": "https://attacker.example",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert private_host not in response.text
    _assert_security_headers(response)


def test_malformed_and_trusted_prefix_hosts_are_rejected() -> None:
    """Не допускает обход allowlist через суффикс после доверенного имени."""
    client = TestClient(create_app(skills_root=_SKILLS_ROOT))
    hostile_hosts = [
        "localhost:443@attacker.example",
        "localhost:80.attacker.example",
        "testserver:evil",
        "testserver:999999",
        f"testserver:{'9' * 5_000}",
        "localhost:0",
        "localhost:65536",
        "localhost:80:90",
        "localhost:",
        "localhost:+80",
        "localhost/path",
        "localhost?query",
        "localhost#fragment",
        "local%68ost",
        " localhost",
        "[::1]",
        "[::1]:8000",
        "::1",
    ]

    responses = [
        client.get("/skills", headers={"host": hostile_host})
        for hostile_host in hostile_hosts
    ]

    assert [response.status_code for response in responses] == [400] * len(responses)
    for response, hostile_host in zip(responses, hostile_hosts, strict=True):
        assert response.json()["error"]["code"] == "bad_request"
        assert hostile_host not in response.text
        _assert_security_headers(response)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:1",
        "localhost:65535",
        "127.0.0.1",
        "127.0.0.1:8000",
        "testserver",
        "testserver:443",
    ],
)
def test_exact_local_host_authorities_remain_allowed(host: str) -> None:
    """Сохраняет точные локальные имена и допустимые десятичные порты."""
    response = TestClient(create_app(skills_root=_SKILLS_ROOT)).get(
        "/health",
        headers={"host": host},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ("base_url", "host", "origin", "expected_status"),
    [
        ("http://testserver", "localhost", "http://localhost", 303),
        ("http://testserver", "localhost", "http://localhost:80", 303),
        ("http://testserver", "localhost:80", "http://localhost", 303),
        (
            "http://testserver",
            "127.0.0.1:65535",
            "http://127.0.0.1:65535",
            303,
        ),
        ("https://testserver", "localhost:443", "https://localhost", 303),
        ("http://testserver", "localhost:8080", "http://localhost", 403),
        ("http://testserver", "localhost", "http://localhost:8080", 403),
        ("http://testserver", "localhost:443", "https://localhost:443", 403),
    ],
)
def test_host_origin_scheme_and_port_matrix(
    base_url: str,
    host: str,
    origin: str,
    expected_status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сверяет scheme/host/default-port до любой файловой перезагрузки."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app, base_url=base_url)
    token = _csrf_token(client)
    original_reload = SkillRegistry.reload
    reload_calls = 0

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal reload_calls
        reload_calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)
    response = client.post(
        "/skills/reload",
        content=f"csrf_token={token}",
        headers={
            "host": host,
            "origin": origin,
            "content-type": "application/x-www-form-urlencoded",
        },
        follow_redirects=False,
    )

    assert response.status_code == expected_status
    assert reload_calls == int(expected_status == 303)
    if expected_status == 303:
        assert urlsplit(response.headers["location"]).path == "/skills"
    else:
        assert response.json()["error"]["code"] == "reload_forbidden"


def test_duplicate_host_authority_is_rejected() -> None:
    """Отклоняет неоднозначный запрос с двумя заголовками Host."""
    response = TestClient(create_app(skills_root=_SKILLS_ROOT)).get(
        "/health",
        headers=[("host", "testserver"), ("host", "localhost")],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    _assert_security_headers(response)


def test_missing_host_authority_is_rejected() -> None:
    """Отклоняет HTTP-запрос без обязательного заголовка Host."""
    messages: list[Message] = []
    scope = cast(
        "Scope",
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": "GET",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50_000),
            "root_path": "",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"",
            "headers": [],
            "state": {},
        },
    )

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    asyncio.run(create_app(skills_root=_SKILLS_ROOT)(scope, receive, send))

    start = messages[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 400
    headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in start["headers"]
    }
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert json.loads(body) == {
        "error": {
            "code": "bad_request",
            "message": "Некорректный запрос.",
        }
    }
    for name, expected in _SECURITY_HEADERS.items():
        assert headers[name] == expected
    assert len(headers["x-request-id"]) == 32


def test_unicode_csrf_is_forbidden_without_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фиксирует 403 и ноль вызовов загрузчика для Unicode-токена."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app)
    before = client.get("/api/skills").json()
    reload_calls = 0
    original_reload = SkillRegistry.reload

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal reload_calls
        reload_calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)
    response = client.post(
        "/skills/reload",
        content=b"csrf_token=%D1%8F",
        headers={
            "origin": "http://testserver",
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reload_forbidden"
    assert reload_calls == 0
    assert client.get("/api/skills").json() == before


def test_reload_marker_is_consumed_after_first_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Требует одноразовый flash-маркер после 303 и чистый canonical refresh."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app)
    token = _csrf_token(client)
    original_reload = SkillRegistry.reload
    reload_calls = 0

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal reload_calls
        reload_calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)
    redirect = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    location = redirect.headers["location"]
    first_get = client.get(location)
    replayed_get = client.get(location)
    canonical_refresh = client.get("/skills")

    assert redirect.status_code == 303
    assert urlsplit(location).path == "/skills"
    assert "Каталог перезагружен" in first_get.text
    assert "Каталог перезагружен" not in replayed_get.text
    assert "Каталог перезагружен" not in canonical_refresh.text
    assert reload_calls == 1


def test_unknown_root_issue_uses_human_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не показывает машинную причину или точку для будущей root-ошибки."""
    app = create_app(skills_root=_SKILLS_ROOT)
    client = TestClient(app)
    report = LoadReport(
        skills=(),
        issues=(LoadIssue(path=".", reason="future_root_reason"),),
    )
    monkeypatch.setattr(SkillRegistry, "reload", lambda _registry: report)

    response = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert "Каталог не удалось загрузить." in response.text
    assert "Проверьте настроенную папку skills" in response.text
    assert "future_root_reason" not in response.text
    assert "Идентификатор:" not in response.text
    assert ">.<" not in response.text


def test_application_route_and_method_surface_is_unchanged() -> None:
    """Фиксирует пять прикладных маршрутов после внутреннего рефакторинга."""
    app = create_app(skills_root=_SKILLS_ROOT)
    included_routers = [
        router
        for route in app.routes
        if isinstance((router := getattr(route, "original_router", None)), APIRouter)
    ]
    assert len(included_routers) == 1
    routes = {
        route.path: frozenset(route.methods or ())
        for route in included_routers[0].routes
        if isinstance(route, APIRoute)
    }

    assert routes == {
        "/": frozenset({"GET"}),
        "/api/skills": frozenset({"GET"}),
        "/health": frozenset({"GET"}),
        "/skills": frozenset({"GET"}),
        "/skills/reload": frozenset({"POST"}),
    }


def test_catalog_uses_only_reachable_same_origin_static_assets() -> None:
    """Проверяет локальные favicon, CSS и JS без внешней поставки."""
    client = TestClient(create_app(skills_root=_SKILLS_ROOT))
    response = client.get("/skills")
    parser = _parse_page(response.text)

    assert len(parser.asset_urls) == 4
    assert parser.dangerous_nodes == []
    for asset_url in parser.asset_urls:
        parsed = urlsplit(asset_url)
        assert parsed.scheme in {"", "http"}
        assert parsed.netloc in {"", "testserver"}
        assert parsed.path.startswith("/static/")
        asset_response = client.get(parsed.path)
        assert asset_response.status_code == 200
        _assert_security_headers(asset_response)


def test_local_javascript_avoids_known_html_execution_sinks() -> None:
    """Расширяет XSS-страж за пределы только innerHTML."""
    scripts = sorted(
        (_SKILLS_ROOT.parent / "src" / "skillhub" / "web" / "static").glob("*.js")
    )
    assert [script.name for script in scripts] == ["app.js"]
    source = scripts[0].read_text(encoding="utf-8")
    forbidden_sinks = [
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "document.writeln",
        "eval(",
        "new Function",
    ]

    assert "textContent" in source
    assert all(sink not in source for sink in forbidden_sinks)
