"""Проверяет публичный шов финализации нормативного протокола."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from httpx2 import Response

from skillhub.app_factory import create_app
from skillhub.core import SkillHubError
from skillhub.llm import FakeLlmGateway
from skillhub.protocol import (
    PLACEHOLDER,
    ExtraClarificationFieldError,
    FinalizedProtocol,
    GeneratedDraft,
    Protocol,
    ProtocolTask,
    UnconfirmedClaim,
    UnknownClarificationIdError,
    UnresolvedClarificationError,
    finalize,
    parse,
)
from skillhub.web import create_protocol_router, install_error_handlers

_WEB_ROOT = Path(__file__).resolve().parents[1] / "src" / "skillhub" / "web"
_PROTOCOL_HTML = _WEB_ROOT / "templates" / "protocol.html"
_PROTOCOL_JS = _WEB_ROOT / "static" / "protocol.js"
_TEMPLATES = _WEB_ROOT / "templates"
_CSRF = "protocol-finalize-csrf-token"
_ORIGIN = {"origin": "http://testserver"}
_VALID_MARKDOWN = f"""# Протокол встречи: Тема

**Дата:** 2026-09-12
**Участники:** Анна

## Обсуждение
- пункт

## Решения
- включить показатели продаж

## Задачи
- {PLACEHOLDER}

## Открытые вопросы
- вопрос
"""
_GAP_MARKDOWN = _VALID_MARKDOWN.replace(
    "**Дата:** 2026-09-12",
    f"**Дата:** {PLACEHOLDER}",
)
_CONFIRMED_MATERIAL = "После паузы решили включить показатели продаж."


def _protocol(**overrides: object) -> Protocol:
    """Собирает протокол с заполненными косметическими полями.

    Args:
        overrides: поля, которые заменяют значения по умолчанию.
    """
    payload: dict[str, object] = {
        "title": "Тема",
        "date": "2026-09-12",
        "participants": ("Анна",),
        "discussion": ("пункт обсуждения",),
        "decisions": ("включить показатели продаж",),
        "tasks": (),
        "open_questions": ("открытый вопрос",),
    }
    payload.update(overrides)
    return Protocol.model_validate(payload)


def _task(
    title: str = "Подготовить отчёт",
    assignee: str = "Анна",
    due: str = "2026-09-15",
) -> ProtocolTask:
    """Собирает задачу протокола.

    Args:
        title: формулировка задачи.
        assignee: ответственный или заполнитель.
        due: срок или заполнитель.
    """
    return ProtocolTask(title=title, assignee=assignee, due=due)


def test_closed_answers_return_final_protocol() -> None:
    """Проверяет, что закрытые строки дают итоговый протокол."""
    protocol = _protocol(date=PLACEHOLDER)
    snapshot = protocol.model_dump()

    result = finalize(
        protocol,
        ({"id": "date", "action": "answer", "value": "2026-10-01"},),
        _CONFIRMED_MATERIAL,
    )

    assert isinstance(result, FinalizedProtocol)
    assert result.protocol.date == "2026-10-01"
    assert result.protocol.participants == ("Анна",)
    assert result.protocol.decisions == ("включить показатели продаж",)
    assert result.unconfirmed == ()
    assert protocol.model_dump() == snapshot


def test_skip_keeps_placeholder_in_final_protocol() -> None:
    """Проверяет, что пропуск оставляет заполнитель в итоговом протоколе."""
    protocol = _protocol(date=PLACEHOLDER)

    result = finalize(
        protocol,
        ({"id": "date", "action": "skip"},),
        _CONFIRMED_MATERIAL,
    )

    assert result.protocol.date == PLACEHOLDER
    assert result.unconfirmed == ()


def test_pending_row_leaves_original_protocol_unchanged() -> None:
    """Проверяет, что незакрытый набор не меняет исходный протокол."""
    protocol = _protocol(date=PLACEHOLDER, participants=())
    snapshot = protocol.model_dump()

    with pytest.raises(UnresolvedClarificationError) as caught:
        finalize(
            protocol,
            ({"id": "date", "action": "skip"},),
            _CONFIRMED_MATERIAL,
        )

    assert isinstance(caught.value, SkillHubError)
    assert caught.value.status_code == 409
    assert protocol.model_dump() == snapshot


def test_foreign_id_leaves_original_protocol_unchanged() -> None:
    """Проверяет отказ чужого идентификатора без записи в протокол."""
    protocol = _protocol(date=PLACEHOLDER)
    snapshot = protocol.model_dump()

    with pytest.raises(UnknownClarificationIdError) as caught:
        finalize(
            protocol,
            ({"id": "title", "action": "answer", "value": "подмена"},),
            _CONFIRMED_MATERIAL,
        )

    assert isinstance(caught.value, SkillHubError)
    assert "title" not in caught.value.public_message
    assert "подмена" not in caught.value.public_message
    assert protocol.model_dump() == snapshot


def test_extra_decision_field_is_rejected() -> None:
    """Проверяет отказ лишнего поля решения."""
    protocol = _protocol(date=PLACEHOLDER)
    snapshot = protocol.model_dump()

    with pytest.raises(ExtraClarificationFieldError) as caught:
        finalize(
            protocol,
            (
                {
                    "id": "date",
                    "action": "answer",
                    "value": "2026-10-01",
                    "title": "скрытая тема",
                },
            ),
            _CONFIRMED_MATERIAL,
        )

    assert isinstance(caught.value, SkillHubError)
    assert "title" not in caught.value.public_message
    assert protocol.model_dump() == snapshot


def test_repeat_finalize_with_same_answers_matches() -> None:
    """Проверяет, что повтор финализации с тем же набором совпадает."""
    protocol = _protocol(date=PLACEHOLDER)
    answers = ({"id": "date", "action": "answer", "value": "2026-10-02"},)

    first = finalize(protocol, answers, _CONFIRMED_MATERIAL)
    second = finalize(protocol, answers, _CONFIRMED_MATERIAL)

    assert first == second
    assert first.protocol.date == "2026-10-02"


def test_unconfirmed_claims_are_returned_not_hidden() -> None:
    """Проверяет, что неподтверждённые утверждения остаются в результате."""
    protocol = _protocol(date=PLACEHOLDER)

    result = finalize(
        protocol,
        ({"id": "date", "action": "answer", "value": "2026-10-01"},),
        "обсуждали другую тему и разошлись",
    )

    assert result.protocol.date == "2026-10-01"
    assert result.unconfirmed == (
        UnconfirmedClaim(
            target="decision:0",
            reason="Утверждение не подтверждено явным маркером в материале.",
        ),
    )


def test_verify_runs_on_applied_protocol() -> None:
    """Проверяет, что после ответа ответственный становится утверждением."""
    protocol = _protocol(
        decisions=(),
        tasks=(_task(assignee=PLACEHOLDER),),
    )

    result = finalize(
        protocol,
        ({"id": "task:0:assignee", "action": "answer", "value": "Анна"},),
        "Поручено подготовить отчёт.",
    )

    assert result.protocol.tasks[0].assignee == "Анна"
    assert [item.target for item in result.unconfirmed] == [
        "task:0:assignee",
        "task:0:due",
    ]


def test_finalize_module_does_not_call_model_or_usage() -> None:
    """Проверяет, что финализация не ссылается на модель и учёт расходов."""
    source = Path(finalize.__code__.co_filename).read_text(encoding="utf-8")

    assert "ProtocolTextGenerator" not in source
    assert "LlmGateway" not in source
    assert "skillhub.llm" not in source
    assert "skillhub.usage" not in source


class _FakeGenerator:
    """Возвращает заранее заданный черновик и запоминает вызовы порта."""

    def __init__(self, draft: GeneratedDraft) -> None:
        """Сохраняет ответ порта.

        Args:
            draft: заранее собранный текстовый ответ.
        """
        self.draft = draft
        self.calls: list[tuple[str, str]] = []

    def generate(self, instruction: str, material: str) -> GeneratedDraft:
        """Возвращает заготовленный черновик.

        Args:
            instruction: доверенная инструкция.
            material: транскрипция встречи.
        """
        self.calls.append((instruction, material))
        return self.draft


def test_finalize_http_returns_text_without_generator() -> None:
    """Проверяет успешную финализацию без порта генерации."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "answer", "value": "2026-10-01"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert parse(payload["text"]).date == "2026-10-01"
    assert payload["unconfirmed"] == []


def test_finalize_http_pending_omits_text() -> None:
    """Проверяет оболочку незакрытых уточнений без поля text."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    body = response.json()
    assert response.status_code == 409
    assert body == {
        "error": {
            "code": "unresolved_clarification",
            "message": "Есть незакрытые уточнения.",
        }
    }
    assert "text" not in body


def test_finalize_http_rejects_foreign_id_without_text() -> None:
    """Проверяет отказ чужого идентификатора без перезаписи текста."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "title", "action": "answer", "value": "подмена"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "unknown_clarification_id"
    assert "text" not in body
    assert "подмена" not in response.text


def test_finalize_http_rejects_extra_field() -> None:
    """Проверяет отказ лишнего поля решения на HTTP-шве."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [
                {
                    "id": "date",
                    "action": "answer",
                    "value": "2026-10-01",
                    "note": "лишнее",
                }
            ],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "extra_clarification_field"
    assert "text" not in body


def test_finalize_http_skip_keeps_placeholder() -> None:
    """Проверяет, что пропуск на HTTP-шве сохраняет заполнитель."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "skip"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert parse(payload["text"]).date == PLACEHOLDER


def test_finalize_http_replay_matches() -> None:
    """Проверяет идемпотентность повторного POST с тем же набором."""
    client = TestClient(create_app())
    token = _csrf(client)
    fields: dict[str, object] = {
        "text": _GAP_MARKDOWN,
        "answers": [{"id": "date", "action": "answer", "value": "2026-10-03"}],
        "material": _CONFIRMED_MATERIAL,
    }

    first = _post_finalize(client, token, fields)
    second = _post_finalize(client, token, fields)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_finalize_does_not_call_generator_or_llm_gateway() -> None:
    """Проверяет отсутствие второго вызова модели и записи в usage."""
    generator = _FakeGenerator(
        GeneratedDraft(text=_VALID_MARKDOWN, finish_reason="stop"),
    )
    gateway = FakeLlmGateway()
    client = TestClient(create_app(llm_gateway=gateway))
    routed = _router_client(generator)
    html = _PROTOCOL_HTML.read_text(encoding="utf-8")
    script = _PROTOCOL_JS.read_text(encoding="utf-8")
    routes_source = Path(create_protocol_router.__code__.co_filename).read_text(
        encoding="utf-8"
    )

    app_response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "skip"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )
    routed_response = _post_finalize(
        routed,
        _CSRF,
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "skip"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    assert app_response.status_code == 200
    assert routed_response.status_code == 200
    assert generator.calls == []
    assert gateway.requests == []
    assert "_require_generator" not in _finalize_route_source(routes_source)
    assert "skillhub.usage" not in routes_source
    assert "Сформировать итоговый протокол" in html
    assert 'id="protocol-finalize-button"' in html
    assert "/protocol/finalize" in script
    assert "innerHTML" not in script
    assert "baseText" in script
    assert "rowMemory" in script
    assert "replayRemaining" in script
    assert "previousText" not in script
    finalize_fn = _function_source(script, "submitFinalize")
    assert finalize_fn.index("if (!response.ok)") < finalize_fn.index("writeMarkdown")
    assert "unconfirmed" in script


def test_finalize_http_rejects_foreign_origin_and_wrong_csrf() -> None:
    """Проверяет отказ финализации при чужом Origin и неверном CSRF."""
    client = TestClient(create_app())
    token = _csrf(client)
    foreign = client.post(
        "/protocol/finalize",
        json={
            "csrf_token": token,
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "skip"}],
            "material": _CONFIRMED_MATERIAL,
        },
        headers={"origin": "https://attacker.example"},
    )
    wrong = _post_finalize(
        client,
        "wrong-csrf-token-value",
        {
            "text": _GAP_MARKDOWN,
            "answers": [{"id": "date", "action": "skip"}],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "protocol_forbidden"
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "protocol_forbidden"


def test_finalize_http_keeps_parse_error_envelope() -> None:
    """Проверяет оболочку разбора на шве финализации."""
    client = TestClient(create_app())
    response = _post_finalize(
        client,
        _csrf(client),
        {
            "text": "это не протокол",
            "answers": [],
            "material": _CONFIRMED_MATERIAL,
        },
    )

    error = response.json()["error"]
    assert response.status_code == 422
    assert error["code"] == "protocol_parse_error"
    assert set(error) == {"code", "message", "line", "expected", "got"}


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


def _csrf(client: TestClient) -> str:
    """Читает CSRF-токен со страницы протокола.

    Args:
        client: клиент одного экземпляра приложения.
    """
    parser = _CsrfParser()
    parser.feed(client.get("/protocol").text)
    assert len(parser.tokens) == 1
    return parser.tokens[0]


def _post_finalize(
    client: TestClient,
    token: str,
    fields: dict[str, object],
) -> Response:
    """Отправляет JSON-запрос финализации с Origin и секретом.

    Args:
        client: HTTP-клиент проверяемого приложения.
        token: ожидаемый секрет CSRF.
        fields: прикладные поля тела запроса.
    """
    return client.post(
        "/protocol/finalize",
        json={"csrf_token": token, **fields},
        headers=_ORIGIN,
    )


def _router_client(generator: _FakeGenerator) -> TestClient:
    """Собирает изолированный маршрутизатор протокола.

    Args:
        generator: подставной порт генерации.
    """
    templates = Jinja2Templates(directory=_TEMPLATES)
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=_WEB_ROOT / "static"), name="static")
    app.include_router(
        create_protocol_router(
            templates,
            csrf_token=_CSRF,
            generator=generator,
        )
    )
    install_error_handlers(app)
    return TestClient(app)


def _finalize_route_source(source: str) -> str:
    """Возвращает исходник обработчика финализации.

    Args:
        source: полный текст модуля маршрутов.
    """
    start = source.index("async def finalize")
    return source[start : start + 600]


def _function_source(script: str, name: str) -> str:
    """Возвращает исходник одной функции страницы.

    Args:
        script: текст protocol.js.
        name: имя функции.
    """
    start = script.index(f"async function {name}")
    nxt = script.find("\nasync function ", start + 1)
    nxt_sync = script.find("\nfunction ", start + 1)
    candidates = [index for index in (nxt, nxt_sync) if index != -1]
    end = min(candidates) if candidates else len(script)
    return script[start:end]
