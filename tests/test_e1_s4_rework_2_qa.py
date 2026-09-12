"""Повторно проверяет закрытие замечаний второго rework E1-S4."""

import ast
import asyncio
from html.parser import HTMLParser
from pathlib import Path
from threading import Event, Lock
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
import yaml
from anyio.lowlevel import checkpoint_if_cancelled as original_checkpoint
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx2 import ASGITransport, AsyncClient, Response

import skillhub.registry._registry as registry_module
import skillhub.web._catalog as catalog_module
import skillhub.web.routes as routes_module
from skillhub import web
from skillhub.app_factory import create_app
from skillhub.registry import LoadIssue, LoadReport, Skill, SkillRegistry

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES_ROOT = _REPOSITORY_ROOT / "src" / "skillhub" / "web" / "templates"
_STATIC_ROOT = _REPOSITORY_ROOT / "src" / "skillhub" / "web" / "static"
_ORIGIN_HEADERS = {"origin": "http://testserver"}
_SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
}


class _CatalogParser(HTMLParser):
    """Извлекает карточки, flash-состояние и токен без сравнения всего HTML."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора."""
        super().__init__()
        self.card_names: list[str] = []
        self.csrf_tokens: list[str] = []
        self.has_reload_result = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Сохраняет только элементы наблюдаемого договора."""
        attributes = dict(attrs)
        if tag == "article" and (name := attributes.get("data-skill-name")):
            self.card_names.append(name)
        if (
            tag == "input"
            and attributes.get("name") == "csrf_token"
            and (token := attributes.get("value"))
        ):
            self.csrf_tokens.append(token)
        if any(name == "data-reload-result" for name, _value in attrs):
            self.has_reload_result = True


def _parse_catalog(html: str) -> _CatalogParser:
    parser = _CatalogParser()
    parser.feed(html)
    return parser


def _write_skill(root: Path, directory: str, name: str) -> None:
    skill_dir = root / directory
    skill_dir.mkdir(parents=True)
    metadata = yaml.safe_dump(
        {
            "name": name,
            "caption": f"Общий режим {name}",
            "description": f"Описание {name}",
        },
        allow_unicode=True,
        sort_keys=False,
    )
    (skill_dir / "SKILL.md").write_text(
        f"---\n{metadata}---\nPrivate body {name}.",
        encoding="utf-8",
    )


def _report(name: str, issue: str | None = None) -> LoadReport:
    issues = (LoadIssue(path=issue, reason="missing_description"),) if issue else ()
    return LoadReport(
        skills=(
            Skill(
                name=name,
                caption=f"Режим {name}",
                description=f"Описание {name}",
                body=f"Закрытое тело {name}.",
                has_files=False,
            ),
        ),
        issues=issues,
    )


def _build_public_app(
    root: Path,
    token: str,
    *,
    load_initial: bool = True,
) -> tuple[FastAPI, SkillRegistry]:
    registry = SkillRegistry(root)
    if load_initial:
        registry.reload()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=_STATIC_ROOT), name="static")
    app.include_router(
        web.create_router(
            Jinja2Templates(directory=_TEMPLATES_ROOT),
            registry=registry,
            csrf_token=token,
        )
    )
    web.install_error_handlers(app)
    return app, registry


async def _csrf_token(client: AsyncClient) -> str:
    parser = _parse_catalog((await client.get("/skills")).text)
    assert len(parser.csrf_tokens) == 1
    return parser.csrf_tokens[0]


def _marker(response_location: str) -> str:
    values = parse_qs(urlsplit(response_location).query).get("reload", [])
    assert len(values) == 1
    return values[0]


def _assert_safe_unavailable_redirect(response: Response) -> None:
    headers = response.headers
    assert response.status_code == 303
    assert headers["location"] == "/skills?notice=reload-unavailable"
    for name, expected in _SECURITY_HEADERS.items():
        assert headers[name] == expected
    assert not any(name.startswith("access-control-") for name in headers)


def test_mismatched_directories_share_canonical_report_registry_and_web_order(
    tmp_path: Path,
) -> None:
    """Не позволяет порядку каталогов стать вторым порядком API и UI."""
    root = tmp_path / "skills"
    _write_skill(root, "alpha-directory", "zeta")
    _write_skill(root, "zeta-directory", "alpha")
    app, registry = _build_public_app(root, "router-secret")
    report = registry.load()

    async def exercise() -> tuple[list[str], list[str]]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            api = await client.get("/api/skills", params={"q": "общий"})
            page = await client.get("/skills", params={"q": "общий"})
        return (
            [item["name"] for item in api.json()],
            _parse_catalog(page.text).card_names,
        )

    api_names, page_names = asyncio.run(exercise())
    expected = ["alpha", "zeta"]

    assert [skill.name for skill in report.search("общий")] == expected
    assert [skill.name for skill in registry.search("общий")] == expected
    assert api_names == page_names == expected


def test_factory_has_no_public_registry_that_can_diverge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не экспортирует второй изменяемый путь публикации через app.state."""
    root = tmp_path / "skills"
    _write_skill(root, "alpha-directory", "alpha")
    captured: list[SkillRegistry] = []
    original_reload = SkillRegistry.reload

    def capture_startup(registry: SkillRegistry) -> LoadReport:
        captured.append(registry)
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", capture_startup)
    app = create_app(skills_root=root)

    assert len(captured) == 1
    assert not hasattr(app.state, "skill_registry")
    assert not any(
        isinstance(value, SkillRegistry)
        for value in vars(app.state).get("_state", {}).values()
    )


def test_public_router_registry_cannot_diverge_from_http_catalog(
    tmp_path: Path,
) -> None:
    """Не допускает второй путь публикации через публичный registry router."""
    root = tmp_path / "skills"
    _write_skill(root, "alpha-directory", "alpha")
    app, registry = _build_public_app(root, "router-secret")
    _write_skill(root, "bravo-directory", "bravo")

    report = registry.reload()

    async def read_http_catalog() -> tuple[list[str], list[str]]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            api = await client.get("/api/skills")
            page = await client.get("/skills")
        return (
            [item["name"] for item in api.json()],
            _parse_catalog(page.text).card_names,
        )

    api_names, page_names = asyncio.run(read_http_catalog())
    expected = [skill.name for skill in report.search("")]

    assert [skill.name for skill in registry.search("")] == expected
    assert api_names == page_names == expected


def test_public_router_tracks_initially_empty_registry_after_direct_reload(
    tmp_path: Path,
) -> None:
    """Показывает прямую публикацию реестра после пустой сборки router."""
    root = tmp_path / "skills"
    app, registry = _build_public_app(
        root,
        "router-secret",
        load_initial=False,
    )
    _write_skill(root, "alpha-directory", "alpha")

    async def exercise() -> tuple[list[str], list[str]]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            empty = await client.get("/api/skills")
            registry.reload()
            loaded = await client.get("/api/skills")
        return (
            [item["name"] for item in empty.json()],
            [item["name"] for item in loaded.json()],
        )

    empty_names, loaded_names = asyncio.run(exercise())

    assert empty_names == []
    assert loaded_names == ["alpha"]


def test_busy_reload_burst_is_immediate_single_flight_and_keeps_worker_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отклоняет весь burst до второго scan и оставляет worker для GET."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app = create_app(skills_root=root)
    loader_entered = Event()
    release_loader = Event()
    probe_entered = Event()
    calls = 0
    guard = Lock()

    def controlled_reload(_root: Path) -> LoadReport:
        nonlocal calls
        with guard:
            calls += 1
        loader_entered.set()
        assert release_loader.wait(timeout=5)
        return _report("loaded")

    @app.get("/qa-worker-probe")
    def worker_probe() -> dict[str, str]:
        probe_entered.set()
        return {"status": "available"}

    monkeypatch.setattr(registry_module, "_load_registry", controlled_reload)

    async def exercise() -> tuple[list[Response], Response, Response]:
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = limiter.total_tokens
        limiter.total_tokens = 2
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                token = await _csrf_token(client)
                active = asyncio.create_task(
                    client.post(
                        "/skills/reload",
                        data={"csrf_token": token},
                        headers=_ORIGIN_HEADERS,
                        follow_redirects=False,
                    )
                )
                assert await asyncio.to_thread(loader_entered.wait, 5)
                busy_requests = [
                    asyncio.create_task(
                        client.post(
                            "/skills/reload",
                            data={"csrf_token": token},
                            headers=_ORIGIN_HEADERS,
                            follow_redirects=False,
                        )
                    )
                    for _ in range(12)
                ]
                probe = asyncio.create_task(client.get("/qa-worker-probe"))
                busy, probe_response = await asyncio.wait_for(
                    asyncio.gather(asyncio.gather(*busy_requests), probe),
                    timeout=5,
                )
                assert probe_entered.is_set()
                assert calls == 1
                release_loader.set()
                active_response = await asyncio.wait_for(active, timeout=5)
            return list(busy), probe_response, active_response
        finally:
            release_loader.set()
            limiter.total_tokens = previous_tokens

    busy, probe, active = asyncio.run(exercise())

    assert active.status_code == 303
    assert probe.json() == {"status": "available"}
    assert len(busy) == 12
    for response in busy:
        _assert_safe_unavailable_redirect(response)
    assert calls == 1


def test_cancellation_after_scan_does_not_reserve_flash_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Погашает отменённый active POST между scan и сохранением marker."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app = create_app(skills_root=root)
    reached_checkpoint = asyncio.Event()
    never_release = asyncio.Event()
    checkpoint_calls = 0
    reload_calls = 0

    async def controlled_checkpoint() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 1:
            reached_checkpoint.set()
            await never_release.wait()
        else:
            await original_checkpoint()

    def counted_reload(_root: Path) -> LoadReport:
        nonlocal reload_calls
        reload_calls += 1
        return _report(f"generation-{reload_calls}")

    monkeypatch.setattr(routes_module, "checkpoint_if_cancelled", controlled_checkpoint)
    monkeypatch.setattr(registry_module, "_load_registry", counted_reload)

    async def exercise() -> tuple[BaseException, list[Response], Response]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            token = await _csrf_token(client)
            cancelled = asyncio.create_task(
                client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=_ORIGIN_HEADERS,
                    follow_redirects=False,
                )
            )
            await asyncio.wait_for(reached_checkpoint.wait(), timeout=5)
            cancelled.cancel()
            cancelled_result = (
                await asyncio.wait_for(
                    asyncio.gather(cancelled, return_exceptions=True),
                    timeout=5,
                )
            )[0]
            accepted = [
                await client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=_ORIGIN_HEADERS,
                    follow_redirects=False,
                )
                for _ in range(8)
            ]
            rejected = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
        assert isinstance(cancelled_result, BaseException)
        return cancelled_result, accepted, rejected

    cancelled_result, accepted, rejected = asyncio.run(exercise())

    assert isinstance(cancelled_result, asyncio.CancelledError)
    assert [response.status_code for response in accepted] == [303] * 8
    _assert_safe_unavailable_redirect(rejected)
    assert reload_calls == 9


def test_random_marker_collision_unknown_replay_and_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сохраняет непрозрачный уникальный marker и атомарно удаляет его."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app = create_app(skills_root=root)
    marker_a = "A" * 43
    marker_b = "B" * 43
    marker_unknown = "C" * 43
    random_values = iter((marker_a, marker_a, marker_b))
    byte_counts: list[int] = []
    reports = iter((_report("alpha", "broken-alpha"), _report("bravo", "broken-bravo")))

    def controlled_marker(byte_count: int) -> str:
        byte_counts.append(byte_count)
        return next(random_values)

    monkeypatch.setattr(catalog_module, "token_urlsafe", controlled_marker)
    monkeypatch.setattr(registry_module, "_load_registry", lambda _root: next(reports))

    async def exercise() -> tuple[
        Response,
        Response,
        Response,
        Response,
        Response,
        Response,
    ]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            token = await _csrf_token(client)
            first = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            second = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            unknown = await client.get(f"/skills?reload={marker_unknown}")
            first_get = await client.get(first.headers["location"])
            replay = await client.get(first.headers["location"])
            second_get = await client.get(second.headers["location"])
        return first, second, unknown, first_get, replay, second_get

    first, second, unknown, first_get, replay, second_get = asyncio.run(exercise())

    assert _marker(first.headers["location"]) == marker_a
    assert _marker(second.headers["location"]) == marker_b
    assert byte_counts == [32, 32, 32]
    assert _parse_catalog(unknown.text).card_names == ["bravo"]
    assert not _parse_catalog(unknown.text).has_reload_result
    assert marker_unknown not in unknown.text
    assert _parse_catalog(first_get.text).card_names == ["alpha"]
    assert _parse_catalog(first_get.text).has_reload_result
    assert "broken-alpha" in first_get.text
    assert "broken-bravo" not in first_get.text
    assert _parse_catalog(replay.text).card_names == ["bravo"]
    assert not _parse_catalog(replay.text).has_reload_result
    assert _parse_catalog(second_get.text).card_names == ["bravo"]
    assert _parse_catalog(second_get.text).has_reload_result
    for response in (unknown, first_get, replay, second_get):
        assert response.headers["cache-control"] == "no-store"


def test_concurrent_consumers_split_flash_and_current_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Даёт старый flash ровно одному GET, а второму — текущее поколение."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app = create_app(skills_root=root)
    reports = iter((_report("alpha"), _report("bravo")))
    monkeypatch.setattr(registry_module, "_load_registry", lambda _root: next(reports))

    async def exercise() -> list[_CatalogParser]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            token = await _csrf_token(client)
            old = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            pages = await asyncio.gather(
                client.get(old.headers["location"]),
                client.get(old.headers["location"]),
            )
        return [_parse_catalog(page.text) for page in pages]

    pages = asyncio.run(exercise())
    observed = sorted((page.card_names, page.has_reload_result) for page in pages)

    assert observed == [(["alpha"], True), (["bravo"], False)]


def test_full_flash_capacity_rejects_before_scan_and_consume_reopens_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не вытесняет старый redirect и допускает новый scan после consume."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app = create_app(skills_root=root)
    calls = 0

    def counted_reload(_root: Path) -> LoadReport:
        nonlocal calls
        calls += 1
        return _report(f"generation-{calls}")

    monkeypatch.setattr(registry_module, "_load_registry", counted_reload)

    async def exercise() -> tuple[list[Response], Response, Response, Response]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            token = await _csrf_token(client)
            redirects = [
                await client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=_ORIGIN_HEADERS,
                    follow_redirects=False,
                )
                for _ in range(8)
            ]
            rejected = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            oldest = await client.get(redirects[0].headers["location"])
            admitted = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
        return redirects, rejected, oldest, admitted

    redirects, rejected, oldest, admitted = asyncio.run(exercise())

    assert [response.status_code for response in redirects] == [303] * 8
    _assert_safe_unavailable_redirect(rejected)
    assert calls == 9
    assert _parse_catalog(oldest.text).card_names == ["generation-1"]
    assert _parse_catalog(oldest.text).has_reload_result
    assert admitted.status_code == 303


@pytest.mark.parametrize("token", ["x", "-_" * 128])
def test_public_router_accepts_ascii_secret_boundaries_and_rejects_unicode_request(
    tmp_path: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Принимает секреты длиной 1/256, но Unicode-запрос даёт 403 до scan."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")
    app, _registry = _build_public_app(root, token)
    calls = 0
    original_reload = SkillRegistry.reload

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal calls
        calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)

    async def exercise() -> tuple[Response, Response]:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            accepted = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            rejected = await client.post(
                "/skills/reload",
                content=b"csrf_token=%D1%8F",
                headers={
                    **_ORIGIN_HEADERS,
                    "content-type": "application/x-www-form-urlencoded",
                },
            )
        return accepted, rejected

    accepted, rejected = asyncio.run(exercise())

    assert accepted.status_code == 303
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "reload_forbidden"
    assert calls == 1


@pytest.mark.parametrize("token", ["", "я", "contains space", "x" * 257])
def test_public_router_rejects_invalid_configured_secret(
    tmp_path: Path,
    token: str,
) -> None:
    """Завершает сборку ошибкой для пустой, Unicode и слишком длинной конфигурации."""
    root = tmp_path / "skills"
    _write_skill(root, "initial-directory", "initial")

    with pytest.raises(ValueError, match="1 до 256"):
        _build_public_app(root, token)


def test_composition_root_uses_complete_public_web_facade() -> None:
    """Фиксирует публичный web-шов без импорта приватной раскладки из root."""
    source = (_REPOSITORY_ROOT / "src" / "skillhub" / "app_factory.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    web_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("skillhub.web")
    }

    assert web_imports == {"skillhub.web"}
    assert set(web.__all__) == {
        "ProcessErrorBoundary",
        "SecurityHeadersMiddleware",
        "StrictHostMiddleware",
        "TrustedHostEnvelopeMiddleware",
        "create_assistant_router",
        "create_protocol_router",
        "create_router",
        "create_settings_router",
        "install_error_handlers",
    }
