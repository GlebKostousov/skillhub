"""Проверяет восстановление flash и понятный отказ третьего rework E1-S4."""

import asyncio
from html.parser import HTMLParser
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from httpx2 import ASGITransport, AsyncClient, Response

import skillhub.registry._registry as registry_module
import skillhub.web._catalog as catalog_module
from skillhub.app_factory import create_app
from skillhub.registry import LoadIssue, LoadReport, Skill
from skillhub.web._catalog import SkillSummary, _CatalogState
from skillhub.web.errors import ReloadUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

_ORIGIN_HEADERS = {"origin": "http://testserver"}


class _CatalogParser(HTMLParser):
    """Извлекает карточки, токен и доступное состояние отказа."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора."""
        super().__init__()
        self.card_names: list[str] = []
        self.csrf_tokens: list[str] = []
        self.has_reload_result = False
        self.has_unavailable_alert = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Сохраняет наблюдаемые элементы страницы."""
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
        if attributes.get("data-reload-unavailable") is not None:
            self.has_unavailable_alert = attributes.get("role") == "alert"


def _parse_catalog(html: str) -> _CatalogParser:
    parser = _CatalogParser()
    parser.feed(html)
    return parser


def _write_skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    metadata = yaml.safe_dump(
        {
            "name": name,
            "caption": f"Режим {name}",
            "description": f"Описание {name}",
        },
        allow_unicode=True,
        sort_keys=False,
    )
    (directory / "SKILL.md").write_text(
        f"---\n{metadata}---\nЗакрытое тело {name}.",  # noqa: RUF001
        encoding="utf-8",
    )


def _report(name: str, *, body: str | None = None) -> LoadReport:
    return LoadReport(
        skills=(
            Skill(
                name=name,
                caption=f"Режим {name}",
                description=f"Описание {name}",
                body=body or f"Закрытое тело {name}.",
                has_files=False,
            ),
        ),
        issues=(LoadIssue(path=f"broken-{name}", reason="missing_description"),),
    )


async def _csrf_token(client: AsyncClient) -> str:
    parser = _parse_catalog((await client.get("/skills")).text)
    assert len(parser.csrf_tokens) == 1
    return parser.csrf_tokens[0]


def _marker(response: Response) -> str:
    values = parse_qs(urlsplit(response.headers["location"]).query).get("reload", [])
    assert len(values) == 1
    return values[0]


def test_flash_retains_only_public_projection() -> None:
    """Не сохраняет в одноразовом результате отчёт или тело инструкций."""
    private_body = "private-body-must-not-be-retained"
    state = _CatalogState(clock=lambda: 10.0)
    marker = state.reserve_reload()

    state.publish_reload(marker, _report("alpha", body=private_body))
    generation = state.consume(marker)

    assert generation is not None
    assert isinstance(generation.skills[0], SkillSummary)
    assert not hasattr(generation, "report")
    assert private_body not in repr(generation)
    assert generation.summary.issues[0].model_dump().keys() == {"message", "action"}
    assert "missing_description" not in repr(generation)
    assert generation.summary.loaded == generation.summary.skipped == 1


def test_discarded_reservation_releases_slot_and_is_not_consumable() -> None:
    """Не держит слот после reserve+cancel и не показывает неопубликованный flash."""
    state = _CatalogState(clock=lambda: 10.0)
    reserved = state.reserve_reload()

    with pytest.raises(ReloadUnavailableError):
        state.reserve_reload()

    state.discard_reservation(reserved)

    assert state.consume(reserved) is None
    replacement = state.reserve_reload()
    state.publish_reload(replacement, _report("bravo"))
    published = state.consume(replacement)

    assert published is not None
    assert [skill.name for skill in published.skills] == ["bravo"]


def test_expired_flashes_free_capacity_without_dropping_fresh_marker() -> None:
    """Очищает истёкшие слоты и сохраняет только что опубликованный результат."""
    now = [1_000.0]
    state = _CatalogState(clock=lambda: now[0])
    expired = []
    for index in range(8):
        marker = state.reserve_reload()
        state.publish_reload(marker, _report(f"old-{index}"))
        expired.append(marker)

    now[0] = 1_299.999
    with pytest.raises(ReloadUnavailableError):
        state.reserve_reload()

    now[0] = 1_300.0
    fresh = state.reserve_reload()
    state.publish_reload(fresh, _report("fresh"))

    assert all(state.consume(marker) is None for marker in expired)
    kept = state.consume(fresh)
    assert kept is not None
    assert [skill.name for skill in kept.skills] == ["fresh"]


def test_eight_abandoned_redirects_expire_and_admission_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Возобновляет reload после TTL, не вытесняя свежие результаты."""
    root = tmp_path / "skills"
    _write_skill(root, "initial")
    now = [1_000.0]
    monkeypatch.setattr(catalog_module, "monotonic", lambda: now[0], raising=False)
    app = create_app(skills_root=root)
    reports = iter(_report(f"generation-{index}") for index in range(1, 10))
    calls = 0

    def load(_root: Path) -> LoadReport:
        nonlocal calls
        calls += 1
        return next(reports)

    monkeypatch.setattr(registry_module, "_load_registry", load)

    async def exercise() -> tuple[
        list[Response],
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
            redirects = [
                await client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=_ORIGIN_HEADERS,
                    follow_redirects=False,
                )
                for _ in range(8)
            ]
            now[0] = 1_299.999
            denied = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            denied_page = await client.get(denied.headers["location"])
            now[0] = 1_300.0
            recovered = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            expired = await client.get(redirects[0].headers["location"])
            fresh = await client.get(recovered.headers["location"])
        return redirects, denied, denied_page, recovered, expired, fresh

    redirects, denied, denied_page, recovered, expired, fresh = asyncio.run(exercise())

    assert [response.status_code for response in redirects] == [303] * 8
    assert denied.status_code == recovered.status_code == 303
    assert denied.headers["location"] == "/skills?notice=reload-unavailable"
    assert _parse_catalog(denied_page.text).has_unavailable_alert
    assert 'href="/skills">Вернуться к каталогу</a>' in denied_page.text
    expired_page = _parse_catalog(expired.text)
    fresh_page = _parse_catalog(fresh.text)
    assert expired_page.card_names == ["generation-9"]
    assert not expired_page.has_reload_result
    assert fresh_page.card_names == ["generation-9"]
    assert fresh_page.has_reload_result
    assert calls == 9


def test_recent_marker_keeps_exact_public_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Показывает свежий результат точно до истечения его TTL."""
    root = tmp_path / "skills"
    _write_skill(root, "initial")
    now = [10.0]
    monkeypatch.setattr(catalog_module, "monotonic", lambda: now[0], raising=False)
    app = create_app(skills_root=root)
    reports = iter((_report("alpha"), _report("bravo")))
    monkeypatch.setattr(registry_module, "_load_registry", lambda _root: next(reports))

    async def exercise() -> Response:
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
            now[0] = 20.0
            await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            now[0] = 309.999
            return await client.get(old.headers["location"])

    page = asyncio.run(exercise())

    assert _parse_catalog(page.text).card_names == ["alpha"]
    assert "broken-alpha" in page.text
    assert "broken-bravo" not in page.text


def test_marker_collisions_stop_before_scan_and_release_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ограничивает генерацию восемью попытками и не блокирует следующий POST."""
    root = tmp_path / "skills"
    _write_skill(root, "initial")
    app = create_app(skills_root=root)
    marker = "A" * 43
    random_calls = 0
    scan_calls = 0
    original_load = cast(
        "Callable[[Path], LoadReport]",
        vars(registry_module)["_load_registry"],
    )

    def collide(byte_count: int) -> str:
        nonlocal random_calls
        random_calls += 1
        assert byte_count == 32
        return marker

    def count_scan(skills_root: Path) -> LoadReport:
        nonlocal scan_calls
        scan_calls += 1
        return original_load(skills_root)

    monkeypatch.setattr(catalog_module, "token_urlsafe", collide)
    monkeypatch.setattr(registry_module, "_load_registry", count_scan)

    async def exercise() -> tuple[Response, Response, Response]:
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
            denied = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
            assert random_calls == 9
            assert scan_calls == 1
            await client.get(first.headers["location"])
            admitted = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=_ORIGIN_HEADERS,
                follow_redirects=False,
            )
        return first, denied, admitted

    first, denied, admitted = asyncio.run(exercise())

    assert _marker(first) == _marker(admitted) == marker
    assert denied.headers["location"] == "/skills?notice=reload-unavailable"
    assert random_calls == 10
    assert scan_calls == 2


def test_busy_reload_redirects_to_catalog_alert_without_waiting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сохраняет каталог и понятный alert при занятой перезагрузке."""
    root = tmp_path / "skills"
    _write_skill(root, "initial")
    app = create_app(skills_root=root)
    loader_entered = Event()
    release_loader = Event()
    calls = 0
    guard = Lock()
    original_load = cast(
        "Callable[[Path], LoadReport]",
        vars(registry_module)["_load_registry"],
    )

    def controlled_load(skills_root: Path) -> LoadReport:
        nonlocal calls
        with guard:
            calls += 1
        loader_entered.set()
        assert release_loader.wait(timeout=5)
        return original_load(skills_root)

    monkeypatch.setattr(registry_module, "_load_registry", controlled_load)

    async def exercise() -> tuple[Response, Response]:
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
            try:
                assert await asyncio.to_thread(loader_entered.wait, 5)
                busy = await client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=_ORIGIN_HEADERS,
                    follow_redirects=False,
                )
                location = busy.headers.get("location", "/skills")
                page = await client.get(location)
            finally:
                release_loader.set()
                await asyncio.wait_for(active, timeout=5)
        return busy, page

    try:
        busy, page = asyncio.run(exercise())
    finally:
        release_loader.set()

    parsed = _parse_catalog(page.text)
    assert busy.status_code == 303
    assert busy.headers["location"] == "/skills?notice=reload-unavailable"
    assert "application/json" not in busy.headers.get("content-type", "")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert parsed.card_names == ["initial"]
    assert parsed.has_unavailable_alert
    assert "<main" in page.text
    assert "Перезагрузка каталога сейчас недоступна" in page.text
    assert 'href="/skills">Вернуться к каталогу</a>' in page.text
    assert "reload_unavailable" not in page.text
    assert '{"error"' not in page.text
    assert calls == 1
