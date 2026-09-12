"""Проверяет публичный каталог скиллов, поиск и безопасную перезагрузку."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from threading import Barrier, Event, Lock
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
import yaml
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from structlog.testing import capture_logs

import skillhub.registry._registry as registry_module
import skillhub.web._catalog as catalog_module
from skillhub.app_factory import create_app
from skillhub.registry import LoadIssue, LoadReport, Skill, SkillRegistry
from skillhub.web import create_router, install_error_handlers

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = _REPOSITORY_ROOT / "skills"
_EXPECTED_SKILLS = {
    "business-message": ("Деловое сообщение", False),
    "meeting-action-items": ("Задачи встречи", False),
    "meeting-protocol": ("Протокол встречи", True),
    "text-summary": ("Краткое резюме", False),
    "text-translation": ("Перевод текста", False),
}


class SkillsPageParser(HTMLParser):
    """Собирает проверяемую структуру страницы каталога."""

    def __init__(self) -> None:
        """Создаёт пустой результат разбора HTML."""
        super().__init__()
        self.card_names: list[str] = []
        self.csrf_tokens: list[str] = []
        self.forms: list[tuple[str, str]] = []
        self.dangerous_nodes: list[str] = []
        self.script_sources: list[str | None] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает карточки, формы, токены и опасные HTML-узлы.

        Args:
            tag: имя открывающего HTML-тега.
            attrs: атрибуты открывающего HTML-тега.
        """
        attributes = dict(attrs)
        self._remember_card(tag, attributes)
        self._remember_csrf(tag, attributes)
        self._remember_form(tag, attributes)
        self._remember_script(tag, attributes)
        self._remember_dangerous_node(tag, attributes)

    def _remember_card(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag != "article":
            return
        if name := attributes.get("data-skill-name"):
            self.card_names.append(name)

    def _remember_csrf(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag != "input" or attributes.get("name") != "csrf_token":
            return
        if token := attributes.get("value"):
            self.csrf_tokens.append(token)

    def _remember_form(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag == "form":
            self.forms.append(
                (
                    (attributes.get("method") or "get").lower(),
                    attributes.get("action") or "",
                )
            )

    def _remember_script(self, tag: str, attributes: dict[str, str | None]) -> None:
        if tag != "script":
            return
        source = attributes.get("src")
        self.script_sources.append(source)
        if source is None:
            self.dangerous_nodes.append("inline-script")

    def _remember_dangerous_node(
        self,
        tag: str,
        attributes: dict[str, str | None],
    ) -> None:
        if tag in {"img", "iframe", "object"}:
            self.dangerous_nodes.append(tag)
        if any(name.lower().startswith("on") for name in attributes):
            self.dangerous_nodes.append(f"{tag}-event-handler")


class _ReloadArrivalSignal:
    """Сигнализирует о входе второго POST в приложение."""

    def __init__(self, app: ASGIApp, second_arrived: Event) -> None:
        """Сохраняет приложение и наблюдаемый сигнал.

        Args:
            app: проверяемое ASGI-приложение.
            second_arrived: событие входа второго запроса перезагрузки.
        """
        self._app = app
        self._second_arrived = second_arrived
        self._guard = Lock()
        self._reload_requests = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Передаёт запрос и отмечает второй вход перезагрузки."""
        if scope["type"] == "http" and scope["path"] == "/skills/reload":
            with self._guard:
                self._reload_requests += 1
                if self._reload_requests == 2:
                    self._second_arrived.set()
        await self._app(scope, receive, send)


def _write_skill(
    root: Path,
    name: str,
    *,
    caption: str | None = None,
    description: str | None = None,
    body: str | None = None,
) -> None:
    """Записывает реальный тестовый каталог через публичный файловый формат.

    Args:
        root: корневой каталог тестового реестра.
        name: каноническое имя и имя каталога скилла.
        caption: подпись режима или тестовое значение по умолчанию.
        description: описание режима или отсутствие обязательного поля.
        body: тело инструкций или тестовое значение по умолчанию.
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, str] = {
        "name": name,
        "caption": caption or f"Режим {name}",
    }
    if description is not None:
        metadata["description"] = description
    content = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
    )
    (directory / "SKILL.md").write_text(
        f"---\n{content}---\n{body or f'Безопасные инструкции {name}.'}",
        encoding="utf-8",
    )


def _write_valid_skill(
    root: Path,
    name: str,
    *,
    caption: str | None = None,
    description: str | None = None,
    body: str | None = None,
) -> None:
    """Записывает валидный тестовый скилл.

    Args:
        root: корневой каталог тестового реестра.
        name: каноническое имя и имя каталога скилла.
        caption: подпись режима или тестовое значение по умолчанию.
        description: описание режима или тестовое значение по умолчанию.
        body: тело инструкций или тестовое значение по умолчанию.
    """
    _write_skill(
        root,
        name,
        caption=caption,
        description=description or f"Описание {name}",
        body=body,
    )


def _page_parser(response_text: str) -> SkillsPageParser:
    """Разбирает страницу каталога для структурных проверок.

    Args:
        response_text: полученный от приложения HTML.

    Returns:
        Собранную структуру страницы.
    """
    parser = SkillsPageParser()
    parser.feed(response_text)
    return parser


def _csrf_token(client: TestClient) -> str:
    """Получает секретный токен перезагрузки из скрытого поля страницы.

    Args:
        client: клиент одного экземпляра приложения.

    Returns:
        Единственный токен формы перезагрузки.
    """
    parser = _page_parser(client.get("/skills").text)
    assert len(parser.csrf_tokens) == 1
    return parser.csrf_tokens[0]


def _post_reload(
    client: TestClient,
    token: str,
    finished: Event | None = None,
) -> Response:
    """Отправляет валидный POST без перехода и отмечает завершение.

    Args:
        client: клиент одного экземпляра приложения.
        token: токен формы этого экземпляра.
        finished: необязательный сигнал готового ответа.

    Returns:
        Ответ непосредственно от маршрута перезагрузки.
    """
    response = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    if finished is not None:
        finished.set()
    return response


def _catalog_app(root: Path) -> tuple[FastAPI, TestClient]:
    """Собирает приложение с доверенным временным корнем.

    Args:
        root: корневой каталог тестового реестра.

    Returns:
        Приложение и клиент его публичных HTTP-швов.
    """
    app = create_app(skills_root=root)
    return app, TestClient(app)


def _router_client(root: Path, csrf_token: str) -> TestClient:
    """Собирает публичный маршрутизатор с явно заданным токеном.

    Args:
        root: корневой каталог тестового реестра.
        csrf_token: проверяемый токен формы перезагрузки.

    Returns:
        Клиент приложения с публично собранными маршрутами.
    """
    registry = SkillRegistry(root)
    registry.reload()
    templates = Jinja2Templates(
        directory=_REPOSITORY_ROOT / "src" / "skillhub" / "web" / "templates"
    )
    app = FastAPI()
    app.include_router(
        create_router(
            templates,
            registry=registry,
            csrf_token=csrf_token,
        )
    )
    install_error_handlers(app)
    return TestClient(app)


def _report(name: str, issue_path: str | None = None) -> LoadReport:
    """Создаёт различимое поколение каталога для конкурентной проверки.

    Args:
        name: имя единственного скилла поколения.
        issue_path: безопасное имя битого соседнего каталога.

    Returns:
        Отчёт с согласованными данными и причиной пропуска.
    """
    issues = (
        (LoadIssue(path=issue_path, reason="missing_description"),)
        if issue_path
        else ()
    )
    return LoadReport(
        skills=(
            Skill(
                name=name,
                caption=f"Режим {name}",
                description=f"Описание {name}",
                body=f"Закрытое тело {name}",
                has_files=False,
            ),
        ),
        issues=issues,
    )


async def _set_worker_tokens(total_tokens: float) -> float:
    """Задаёт ёмкость общего пула и возвращает прежнее значение."""
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous = limiter.total_tokens
    limiter.total_tokens = total_tokens
    return previous


def test_repository_contains_exactly_five_real_skill_directories() -> None:
    """Проверяет канонический набор артефактов и признаки дополнительных файлов."""
    directories = sorted(path.name for path in _SKILLS_ROOT.iterdir() if path.is_dir())

    assert directories == sorted(_EXPECTED_SKILLS)
    report = SkillRegistry(_SKILLS_ROOT).load()
    assert report.issues == ()
    assert {
        skill.name: (skill.caption, skill.has_files) for skill in report.skills
    } == _EXPECTED_SKILLS
    assert sorted(
        path.relative_to(_SKILLS_ROOT).as_posix()
        for path in (_SKILLS_ROOT / "meeting-protocol" / "references").iterdir()
    ) == [
        "meeting-protocol/references/example.md",
        "meeting-protocol/references/protocol-format.md",
    ]


def test_meeting_protocol_skill_body_includes_grammar_v1() -> None:
    """Проверяет, что тело скилла содержит грамматику v1, а не только ссылку."""
    report = SkillRegistry(_SKILLS_ROOT).load()
    body = next(skill.body for skill in report.skills if skill.name == "meeting-protocol")
    grammar = (_SKILLS_ROOT / "meeting-protocol" / "references" / "protocol-format.md").read_text(
        encoding="utf-8"
    )

    assert "Формат протокола v1" in body
    assert "## Обсуждение" in body
    assert "## Решения" in body
    assert "## Задачи" in body
    assert "## Открытые вопросы" in body
    assert "| Задача | Ответственный | Срок |" in body
    assert "- —" in body
    for line in grammar.splitlines():
        if line.strip():
            assert line in body


def test_default_api_exposes_only_public_metadata_in_stable_order() -> None:
    """Проверяет стартовую загрузку, порядок и отсутствие тела или пути в JSON."""
    response = TestClient(create_app()).get("/api/skills")

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == sorted(_EXPECTED_SKILLS)
    assert all(
        set(item) == {"name", "caption", "description", "has_files"} for item in payload
    )
    assert all(item["description"] for item in payload)
    assert "Безопасные инструкции" not in response.text
    assert str(_SKILLS_ROOT) not in response.text


def test_page_and_api_use_identical_search_result_without_duplicate_cards() -> None:
    """Проверяет единый порядок результатов API и серверного интерфейса."""
    client = TestClient(create_app())

    api_response = client.get("/api/skills", params={"q": "meeting"})
    page_response = client.get("/skills", params={"q": "meeting"})
    parser = _page_parser(page_response.text)
    api_names = [item["name"] for item in api_response.json()]

    assert api_names == ["meeting-action-items", "meeting-protocol"]
    assert parser.card_names == api_names
    assert len(parser.card_names) == len(set(parser.card_names))
    assert "Найдено: 2" in page_response.text


def test_report_registry_and_web_share_frontmatter_name_order(tmp_path: Path) -> None:
    """Сверяет порядок поиска при несовпадении каталога и поля name."""
    root = tmp_path / "skills"
    _write_valid_skill(
        root,
        "zeta",
        caption="Общий режим Z",
    )
    (root / "zeta").rename(root / "alpha-directory")
    _write_valid_skill(
        root,
        "alpha",
        caption="Общий режим A",
    )
    (root / "alpha").rename(root / "zeta-directory")
    registry = SkillRegistry(root)
    report = registry.reload()
    client = TestClient(create_app(skills_root=root))

    report_names = tuple(skill.name for skill in report.search("общий"))
    registry_names = tuple(skill.name for skill in registry.search("общий"))
    api_names = tuple(
        item["name"] for item in client.get("/api/skills", params={"q": "общий"}).json()
    )
    page_names = tuple(
        _page_parser(client.get("/skills", params={"q": "общий"}).text).card_names
    )

    assert (
        report_names
        == registry_names
        == api_names
        == page_names
        == (
            "alpha",
            "zeta",
        )
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ACTION", ["meeting-action-items"]),
        ("рЕзЮмЕ", ["text-summary"]),
        ("", sorted(_EXPECTED_SKILLS)),
    ],
)
def test_search_is_case_insensitive_by_name_and_caption(
    query: str,
    expected: list[str],
) -> None:
    """Проверяет поиск по имени, подписи и пустому запросу."""
    response = TestClient(create_app()).get("/api/skills", params={"q": query})

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == expected


def test_no_match_has_clear_empty_state_in_api_and_page() -> None:
    """Проверяет явное пустое состояние без подмены поисковой выдачи."""
    client = TestClient(create_app())

    api_response = client.get("/api/skills", params={"q": "ничего-не-найдено"})
    page_response = client.get("/skills", params={"q": "ничего-не-найдено"})

    assert api_response.json() == []
    assert _page_parser(page_response.text).card_names == []
    assert "По вашему запросу скиллы не найдены." in page_response.text
    assert "Очистить поиск" in page_response.text


def test_over_limit_query_is_consistent_safe_and_does_not_change_catalog() -> None:
    """Проверяет общий 422-контракт, отсутствие логирования и неизменность снимка."""
    private_query = "private-query-" + "я" * 129
    client = TestClient(create_app())
    before = client.get("/api/skills").json()

    with capture_logs() as logs:
        api_response = client.get("/api/skills", params={"q": private_query})
        page_response = client.get("/skills", params={"q": private_query})

    expected_message = "Поисковый запрос не должен превышать 128 символов."
    assert api_response.status_code == page_response.status_code == 422
    assert api_response.json() == {
        "error": {"code": "query_too_long", "message": expected_message}
    }
    assert expected_message in page_response.text
    assert private_query not in repr(logs)
    assert client.get("/api/skills").json() == before


def test_startup_reload_and_repeated_valid_reload_do_not_duplicate_cards(
    tmp_path: Path,
) -> None:
    """Проверяет стартовую публикацию и идемпотентный POST reload."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    _app, client = _catalog_app(root)
    assert [item["name"] for item in client.get("/api/skills").json()] == ["alpha"]
    _write_valid_skill(root, "bravo")
    token = _csrf_token(client)

    first = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
    )
    second = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
    )

    assert first.status_code == second.status_code == 200
    assert [response.status_code for response in second.history] == [303]
    assert urlsplit(str(second.url)).path == "/skills"
    assert "Загружено: 2" in second.text
    assert "Пропущено: 0" in second.text
    assert _page_parser(second.text).card_names == ["alpha", "bravo"]
    assert [item["name"] for item in client.get("/api/skills").json()] == [
        "alpha",
        "bravo",
    ]


@pytest.mark.parametrize("configured_token", ["short_token-7", "a" * 256])
def test_public_router_accepts_urlsafe_csrf_and_rejects_unicode_request(
    tmp_path: Path,
    configured_token: str,
) -> None:
    """Сохраняет честный контракт явно заданного токена маршрутизатора."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    client = _router_client(root, configured_token)

    accepted = client.post(
        "/skills/reload",
        data={"csrf_token": configured_token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    rejected = client.post(
        "/skills/reload",
        content=b"csrf_token=%D1%8F",
        headers={
            "origin": "http://testserver",
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert accepted.status_code == 303
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "reload_forbidden"


@pytest.mark.parametrize("token", ["", "токен", "contains space", "a" * 257])
def test_public_router_rejects_invalid_configured_csrf(
    tmp_path: Path,
    token: str,
) -> None:
    """Отклоняет неверный секрет при сборке маршрутизатора."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")

    with pytest.raises(ValueError, match="CSRF"):
        _router_client(root, token)


def test_overlapping_reload_is_rejected_and_next_reload_is_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отклоняет конкурентный POST без повторного сканирования."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "initial")
    _app, client = _catalog_app(root)
    token = _csrf_token(client)
    reports = [_report("alpha", "broken-alpha"), _report("bravo", "broken-bravo")]
    loader_entered = Event()
    release_loader = Event()
    overlapping_finished = Event()
    guard = Lock()
    calls = 0

    def controlled_reload(_root: Path) -> LoadReport:
        nonlocal calls
        with guard:
            index = calls
            calls += 1
        if index == 0:
            loader_entered.set()
            assert release_loader.wait(timeout=5)
        return reports[index]

    monkeypatch.setattr(registry_module, "_load_registry", controlled_reload)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_post_reload, client, token)
        assert loader_entered.wait(timeout=5)
        second_future = executor.submit(
            _post_reload,
            client,
            token,
            overlapping_finished,
        )
        try:
            assert overlapping_finished.wait(timeout=5)
            second_response = second_future.result(timeout=5)
            assert calls == 1
        finally:
            release_loader.set()
        first_response = first_future.result(timeout=5)

    third_response = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    first_page = client.get(first_response.headers["location"])
    third_page = client.get(third_response.headers["location"])

    assert first_response.status_code == third_response.status_code == 303
    assert second_response.status_code == 303
    assert second_response.headers["location"] == ("/skills?notice=reload-unavailable")
    assert _page_parser(first_page.text).card_names == ["alpha"]
    assert "broken-alpha" in first_page.text
    assert "broken-bravo" not in first_page.text
    assert _page_parser(third_page.text).card_names == ["bravo"]
    assert "broken-bravo" in third_page.text
    assert "broken-alpha" not in third_page.text
    assert calls == 2


def test_busy_reload_does_not_wait_or_consume_worker_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отклоняет повторный POST вне общего пула рабочих потоков."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "initial")
    app = create_app(skills_root=root)
    loader_entered = Event()
    release_loader = Event()
    second_arrived = Event()
    busy_finished = Event()
    probe_entered = Event()
    calls: list[None] = []

    def controlled_reload(_root: Path) -> LoadReport:
        calls.append(None)
        loader_entered.set()
        assert release_loader.wait(timeout=5)
        return _report(f"generation-{len(calls)}")

    @app.get("/worker-probe")
    def worker_probe() -> dict[str, str]:
        probe_entered.set()
        return {"status": "ok"}

    monkeypatch.setattr(registry_module, "_load_registry", controlled_reload)
    observed_app = _ReloadArrivalSignal(app, second_arrived)

    with TestClient(observed_app) as client:
        token = _csrf_token(client)
        assert client.portal is not None
        previous_tokens = client.portal.call(_set_worker_tokens, 1)
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                first = executor.submit(_post_reload, client, token)
                assert loader_entered.wait(timeout=5)

                busy = executor.submit(_post_reload, client, token, busy_finished)
                assert second_arrived.wait(timeout=5)
                client.portal.call(_set_worker_tokens, 2)
                assert busy_finished.wait(timeout=5)
                busy_response = busy.result(timeout=5)
                probe = executor.submit(client.get, "/worker-probe")
                assert probe_entered.wait(timeout=5)
                release_loader.set()
                first_response = first.result(timeout=5)
                probe_response = probe.result(timeout=5)
        finally:
            release_loader.set()
            client.portal.call(_set_worker_tokens, previous_tokens)

    assert first_response.status_code == 303
    assert busy_response.status_code == 303
    assert busy_response.headers["location"] == "/skills?notice=reload-unavailable"
    assert probe_response.json() == {"status": "ok"}
    assert len(calls) == 1


def test_cancelled_active_reload_releases_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Освобождает допуск после отмены уже начатой перезагрузки."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "initial")
    app = create_app(skills_root=root)
    loader_entered = Event()
    release_loader = Event()
    call_guard = Lock()
    calls = 0

    def controlled_reload(_root: Path) -> LoadReport:
        nonlocal calls
        with call_guard:
            calls += 1
            call_number = calls
        if call_number == 1:
            loader_entered.set()
            assert release_loader.wait(timeout=5)
        return _report(f"generation-{call_number}")

    monkeypatch.setattr(registry_module, "_load_registry", controlled_reload)

    async def exercise_cancellation() -> tuple[list[int], int, object]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            token = _page_parser((await client.get("/skills")).text).csrf_tokens[0]
            headers = {"origin": "http://testserver"}
            first = asyncio.create_task(
                client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=headers,
                    follow_redirects=False,
                )
            )
            entered = await asyncio.to_thread(loader_entered.wait, 5)
            assert entered
            first.cancel()
            release_loader.set()
            first_result = (
                await asyncio.wait_for(
                    asyncio.gather(first, return_exceptions=True),
                    timeout=5,
                )
            )[0]
            accepted = [
                await client.post(
                    "/skills/reload",
                    data={"csrf_token": token},
                    headers=headers,
                    follow_redirects=False,
                )
                for _ in range(8)
            ]
            rejected = await client.post(
                "/skills/reload",
                data={"csrf_token": token},
                headers=headers,
                follow_redirects=False,
            )
        return (
            [response.status_code for response in accepted],
            rejected.status_code,
            first_result,
        )

    accepted_statuses, rejected_status, cancelled_result = asyncio.run(
        exercise_cancellation()
    )

    assert accepted_statuses == [303] * 8
    assert rejected_status == 303
    assert isinstance(cancelled_result, asyncio.CancelledError)
    assert calls == 9


def test_partial_reload_keeps_five_and_shows_only_safe_issue(tmp_path: Path) -> None:
    """Проверяет понятный частичный результат без утечки битого тела и пути."""
    root = tmp_path / "skills"
    for name in _EXPECTED_SKILLS:
        _write_valid_skill(root, name)
    _app, client = _catalog_app(root)
    private_body = "private-broken-body-4219"
    _write_skill(
        root,
        "broken-sixth",
        description=None,
        body=private_body,
    )

    response = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert "Загружено: 5" in response.text
    assert "Пропущено: 1" in response.text
    assert "broken-sixth" in response.text
    assert "В режиме" in response.text  # noqa: RUF001
    assert "не заполнено описание" in response.text
    assert "missing_description" not in response.text
    assert private_body not in response.text
    assert str(tmp_path) not in response.text
    assert _page_parser(response.text).card_names == sorted(_EXPECTED_SKILLS)


def test_unknown_issue_code_uses_safe_human_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Показывает безопасное действие для будущей причины пропуска."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "initial")
    _app, client = _catalog_app(root)
    report = LoadReport(
        skills=(),
        issues=(LoadIssue(path="safe-mode", reason="future_reason"),),
    )
    monkeypatch.setattr(SkillRegistry, "reload", lambda _registry: report)

    response = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert "Не удалось загрузить режим" in response.text  # noqa: RUF001
    assert "safe-mode" in response.text
    assert "Проверьте структуру его файла SKILL.md" in response.text  # noqa: RUF001
    assert "future_reason" not in response.text


def test_missing_root_is_empty_safe_state_before_and_after_reload(
    tmp_path: Path,
) -> None:
    """Проверяет отсутствие 500 для отсутствующего корня скиллов."""
    _app, client = _catalog_app(tmp_path / "missing")

    initial_page = client.get("/skills")
    reload_page = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
    )

    assert initial_page.status_code == reload_page.status_code == 200
    assert client.get("/api/skills").json() == []
    assert "Каталог пока пуст." in initial_page.text
    assert "Папка каталога не найдена." in initial_page.text
    assert "папку" in initial_page.text
    assert "skills" in initial_page.text
    assert "root_missing" not in initial_page.text
    assert "Идентификатор:" not in initial_page.text
    assert ">.<" not in initial_page.text
    assert "Загружено: 0" in reload_page.text
    assert "Пропущено: 1" in reload_page.text


@pytest.mark.parametrize(
    ("origin", "host", "submitted"),
    [
        ("http://testserver", "testserver", None),
        ("http://testserver", "testserver", "wrong"),
        (None, "testserver", "expected"),
        ("null", "testserver", "expected"),
        ("https://attacker.example", "testserver", "expected"),
        ("http://testserver.attacker.example", "testserver", "expected"),
        ("http://testserver:0", "testserver", "expected"),
        ("http://testserver", "localhost", "expected"),
    ],
)
def test_invalid_origin_or_csrf_is_blocked_before_reload(
    tmp_path: Path,
    origin: str | None,
    host: str,
    submitted: str | None,
) -> None:
    """Проверяет запрет мутации для атакующих вариантов Origin и CSRF."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    _app, client = _catalog_app(root)
    token = _csrf_token(client)
    _write_valid_skill(root, "bravo")
    headers = {"host": host}
    if origin is not None:
        headers["origin"] = origin
    if submitted is None:
        body = "unrelated=value"
    else:
        body = f"csrf_token={token if submitted == 'expected' else submitted}"

    response = client.post(
        "/skills/reload",
        content=body,
        headers={
            **headers,
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reload_forbidden"
    assert [item["name"] for item in client.get("/api/skills").json()] == ["alpha"]


def test_valid_origin_and_token_reload_without_secret_in_log_or_url(
    tmp_path: Path,
) -> None:
    """Проверяет секретный токен перезагрузки и его отсутствие в журнале и URL."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    first_app, client = _catalog_app(root)
    second_app, second_client = _catalog_app(root)
    token = _csrf_token(client)
    second_token = _csrf_token(second_client)
    assert token != second_token
    assert len(token) >= 32
    _write_valid_skill(root, "bravo")

    with capture_logs() as logs:
        response = client.post(
            "/skills/reload",
            data={"csrf_token": token},
            headers={"origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert [item.status_code for item in response.history] == [303]
    assert token not in repr(logs)
    assert token not in str(response.url)
    assert urlsplit(str(response.url)).path == "/skills"
    assert urlsplit(str(response.history[0].headers["location"])).path == "/skills"
    assert first_app is not second_app
    assert not hasattr(first_app.state, "skill_registry")
    assert not hasattr(second_app.state, "skill_registry")


def test_reload_uses_prg_once_and_focuses_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Завершает POST перенаправлением и не повторяет его при обновлении."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    _app, client = _catalog_app(root)
    token = _csrf_token(client)
    original_reload = SkillRegistry.reload
    calls = 0

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal calls
        calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)
    redirect = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    location = redirect.headers["location"]
    parsed_location = urlsplit(location)
    redirected_page = client.get(location)
    refreshed_page = client.get("/skills")
    script = client.get("/static/app.js")

    assert redirect.status_code == 303
    assert parsed_location.path == "/skills"
    assert set(parse_qs(parsed_location.query)) == {"reload"}
    assert token not in location
    assert redirected_page.status_code == refreshed_page.status_code == 200
    assert 'data-reload-result tabindex="-1"' in redirected_page.text
    assert "Каталог перезагружен" in redirected_page.text
    assert "Каталог перезагружен" not in refreshed_page.text
    assert ".focus(" in script.text
    assert "history.replaceState" in script.text
    assert calls == 1


def test_reload_marker_uses_csprng_and_unknown_value_has_no_flash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выдаёт непрозрачный маркер и не отражает неизвестное значение."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    opaque_marker = "A" * 43
    monkeypatch.setattr(
        catalog_module,
        "token_urlsafe",
        lambda byte_count: opaque_marker if byte_count == 32 else "",
    )
    client = TestClient(create_app(skills_root=root))
    redirect = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    marker = parse_qs(urlsplit(redirect.headers["location"]).query)["reload"][0]
    unknown = client.get(f"/skills?reload={'B' * 43}")
    issued = client.get(redirect.headers["location"])

    assert marker == opaque_marker
    assert "Каталог перезагружен" not in unknown.text
    assert opaque_marker not in unknown.text
    assert "Каталог перезагружен" in issued.text
    assert issued.headers["cache-control"] == "no-store"


def test_concurrent_get_consumes_reload_marker_once(tmp_path: Path) -> None:
    """Показывает одноразовый результат только одному конкурентному GET."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    client = TestClient(create_app(skills_root=root))
    redirect = client.post(
        "/skills/reload",
        data={"csrf_token": _csrf_token(client)},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    barrier = Barrier(2)

    def consume_marker() -> str:
        barrier.wait(timeout=5)
        return client.get(redirect.headers["location"]).text

    with ThreadPoolExecutor(max_workers=2) as executor:
        pages = tuple(executor.map(lambda _index: consume_marker(), range(2)))

    assert sum("Каталог перезагружен" in page for page in pages) == 1


def test_outstanding_flash_capacity_rejects_reload_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не вытесняет перенаправление при заполненном хранилище результатов."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    client = TestClient(create_app(skills_root=root))
    token = _csrf_token(client)
    original_reload = SkillRegistry.reload
    calls = 0

    def counted_reload(registry: SkillRegistry) -> LoadReport:
        nonlocal calls
        calls += 1
        return original_reload(registry)

    monkeypatch.setattr(SkillRegistry, "reload", counted_reload)
    redirects = [
        client.post(
            "/skills/reload",
            data={"csrf_token": token},
            headers={"origin": "http://testserver"},
            follow_redirects=False,
        )
        for _ in range(8)
    ]
    rejected = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    oldest = client.get(redirects[0].headers["location"])

    assert all(response.status_code == 303 for response in redirects)
    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/skills?notice=reload-unavailable"
    assert calls == 8
    assert "Каталог перезагружен" in oldest.text


def test_old_reload_marker_keeps_exact_generation_after_later_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Показывает точное старое поколение после более новой публикации."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "initial")
    client = TestClient(create_app(skills_root=root))
    token = _csrf_token(client)
    reports = iter((_report("alpha"), _report("bravo")))
    monkeypatch.setattr(registry_module, "_load_registry", lambda _root: next(reports))

    first = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    second = client.post(
        "/skills/reload",
        data={"csrf_token": token},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    first_page = client.get(first.headers["location"])
    current_page = client.get("/skills")

    assert first.status_code == second.status_code == 303
    assert _page_parser(first_page.text).card_names == ["alpha"]
    assert _page_parser(current_page.text).card_names == ["bravo"]


def test_oversized_reload_body_is_rejected_without_mutation(tmp_path: Path) -> None:
    """Проверяет фактический предел тела до перезагрузки."""
    root = tmp_path / "skills"
    _write_valid_skill(root, "alpha")
    _app, client = _catalog_app(root)
    _write_valid_skill(root, "bravo")

    response = client.post(
        "/skills/reload",
        content=b"x" * 4_097,
        headers={
            "origin": "http://testserver",
            "content-type": "application/x-www-form-urlencoded",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert [item["name"] for item in client.get("/api/skills").json()] == ["alpha"]


def test_hostile_metadata_is_text_in_html_and_json(tmp_path: Path) -> None:
    """Проверяет контекстное экранирование HTML и отсутствие sink для скриптов."""
    root = tmp_path / "skills"
    caption = "</h2><script>alert(1)</script>"
    description = '"><img src=x onerror=alert(2)>'
    body = "private-hostile-body-77"
    _write_valid_skill(
        root,
        "hostile",
        caption=caption,
        description=description,
        body=body,
    )
    _app, client = _catalog_app(root)

    api_response = client.get("/api/skills")
    page_response = client.get("/skills")
    parser = _page_parser(page_response.text)
    script_response = client.get("/static/app.js")

    assert api_response.json() == [
        {
            "name": "hostile",
            "caption": caption,
            "description": description,
            "has_files": False,
        }
    ]
    assert body not in api_response.text
    assert "<script>alert(1)</script>" not in page_response.text
    assert "<img src=x" not in page_response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page_response.text
    assert parser.dangerous_nodes == []
    assert len(parser.script_sources) == 1
    assert (parser.script_sources[0] or "").endswith("/static/app.js")
    assert script_response.status_code == 200
    assert "textContent" in script_response.text
    assert "innerHTML" not in script_response.text


def test_skills_page_has_accessible_semantics_and_local_reloading_state() -> None:
    """Проверяет доступную структуру формы, навигации и живых состояний."""
    response = TestClient(create_app()).get("/skills")
    parser = _page_parser(response.text)

    assert response.status_code == 200
    assert '<html lang="ru">' in response.text
    assert "<title>Скиллы — SkillHub</title>" in response.text
    assert '<nav aria-label="Основная навигация">' in response.text
    assert '<main id="content" tabindex="-1"' in response.text
    assert "<h1" in response.text
    assert "Скиллы" in response.text
    assert "Ищите режим по названию или системному имени" in response.text
    assert "каноническое имя" not in response.text
    assert "снимка реестра" not in response.text
    assert 'for="skills-query"' in response.text
    assert 'id="skills-query"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'role="status"' in response.text
    assert ("get", "/skills") in parser.forms
    assert ("post", "/skills/reload") in parser.forms
    assert '<a href="/">Главная</a>' in response.text
    assert response.text.count('name="csrf_token"') == 1


def test_catalog_css_has_narrow_viewport_and_visible_focus_guards() -> None:
    """Проверяет отсутствие фиксированной ширины карточек на экране 320 px."""
    response = TestClient(create_app()).get("/static/app.css")

    assert response.status_code == 200
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in response.text
    assert "overflow-wrap: anywhere" in response.text
    assert ":focus-visible" in response.text
    assert "min-height: 44px" in response.text
    assert "min-width: 320px" not in response.text


def test_catalog_routes_preserve_security_headers_cors_and_host_boundary() -> None:
    """Проверяет наследование HTTP-защиты новыми маршрутами."""
    client = TestClient(create_app())

    response = client.get(
        "/api/skills",
        headers={"origin": "https://attacker.example"},
    )
    hostile_host = client.get("/skills", headers={"host": "attacker.example"})

    assert response.status_code == 200
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert len(response.headers["x-request-id"]) == 32
    assert not any(header.startswith("access-control-") for header in response.headers)
    assert hostile_host.status_code == 400
    assert "SkillHub" not in hostile_host.text


def test_home_page_links_to_catalog_from_navigation() -> None:
    """Проверяет доступный переход с корневой страницы в каталог."""
    client = TestClient(create_app())
    response = client.get("/")

    assert response.status_code == 200
    assert '<nav aria-label="Основная навигация">' in response.text
    assert '<a href="/skills">Скиллы</a>' in response.text
    assert 'href="http://testserver/static/favicon.svg"' in response.text
    assert client.get("/static/favicon.svg").status_code == 200
