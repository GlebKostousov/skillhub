"""Проверяет страницу расходов, JSON-журнал и проводку дневного лимита."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from skillhub.app_factory import create_app
from skillhub.core import configure_logging
from skillhub.llm import FakeLlmGateway, LlmResult, LlmUsage
from skillhub.usage import UsageEvent, UsageLedger
from skillhub.web.usage_routes import _committed_cost

_PUBLIC_BUDGET_MESSAGE = "Дневной лимит модельных расходов исчерпан"
_EMPTY_COPY = "Записей расходов пока нет"


@pytest.fixture
def usage_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Направляет журнал расходов в изолированный файл теста."""
    path = tmp_path / "usage.sqlite3"
    monkeypatch.setenv("SKILLHUB_USAGE_PATH", str(path))
    return path


def test_empty_journal_uses_null_zero_and_empty_copy(usage_path: Path) -> None:
    """Проверяет пустой журнал в JSON и понятный empty state на странице."""
    del usage_path
    client = TestClient(create_app())

    payload = client.get("/api/usage").json()
    page = client.get("/usage")

    assert payload["current"] is None
    assert payload["today"]["committed_nanos"] == 0
    assert payload["today"]["reserved_nanos"] == 0
    assert payload["today"]["limit_nanos"] is None
    assert payload["today"]["currency"] == "USD"
    assert payload["today"]["date"] == datetime.now(UTC).date().isoformat()
    assert payload["entries"] == []
    assert page.status_code == 200
    assert "<h1" in page.text
    assert "Расходы" in page.text
    assert "n/a" in page.text
    assert _EMPTY_COPY in page.text
    assert "intent" not in page.text
    assert "material" not in page.text
    assert "output" not in page.text


def test_recorded_day_sum_matches_json_html_and_sqlite(usage_path: Path) -> None:
    """Проверяет, что итог дня совпадает в JSON, HTML и SUM committed UTC."""
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)
    first = _committed_event("req-today-a", 111, today)
    second = _committed_event("req-today-b", 222, today)
    older = _committed_event("req-yesterday", 999, yesterday)
    ledger = UsageLedger(usage_path)
    ledger.record(older)
    ledger.record(first)
    ledger.record(second)
    client = TestClient(create_app())

    payload = client.get("/api/usage").json()
    page = client.get("/usage")
    utc_day = today.date().isoformat()
    sqlite_sum = _sum_committed_utc(usage_path, utc_day)

    assert payload["today"]["committed_nanos"] == 333
    assert payload["today"]["committed_nanos"] == sqlite_sum
    assert str(payload["today"]["committed_nanos"]) in page.text
    assert [item["request_id"] for item in payload["entries"]] == [
        "req-today-b",
        "req-today-a",
        "req-yesterday",
    ]
    assert "intent" not in json.dumps(payload)
    assert "material" not in json.dumps(payload)


def test_request_id_selects_current_or_stays_null(usage_path: Path) -> None:
    """Проверяет выбор current по request_id и n/a для чужого идентификатора."""
    event = _committed_event("req-current", 450, datetime.now(UTC))
    UsageLedger(usage_path).record(event)
    client = TestClient(create_app())

    found = client.get("/api/usage", params={"request_id": "req-current"}).json()
    missing = client.get("/api/usage", params={"request_id": "req-unknown"}).json()
    found_page = client.get("/usage", params={"request_id": "req-current"})
    missing_page = client.get("/usage", params={"request_id": "req-unknown"})

    assert found["current"] is not None
    assert found["current"]["request_id"] == "req-current"
    assert found["current"]["cost_nanos"] == 450
    assert found["current"]["currency"] == "USD"
    assert missing["current"] is None
    assert "req-current" in found_page.text
    assert "450" in found_page.text
    assert "n/a" in missing_page.text


def test_navigation_includes_usage_tab(usage_path: Path) -> None:
    """Проверяет вкладку «Расходы» в основной навигации."""
    del usage_path
    page = TestClient(create_app()).get("/skills")

    assert '<nav aria-label="Основная навигация">' in page.text
    assert '<a href="/skills">Скиллы</a>' in page.text
    assert '<a href="/usage">Расходы</a>' in page.text


def test_tiny_daily_budget_rejects_assistant_before_model(
    usage_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет 429 и точный public_message при крошечном дневном лимите."""
    del usage_path
    monkeypatch.setenv("SKILLHUB_DAILY_BUDGET_NANOS", "1")
    gateway = FakeLlmGateway(result=_llm_result())
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)

    response = _post_assistant(client, token)

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "daily_budget_exceeded",
            "message": _PUBLIC_BUDGET_MESSAGE,
        }
    }
    assert gateway.requests == []


def test_usage_complete_log_keeps_cost_and_hides_content(
    usage_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет usage.complete без контента и без зачистки стоимости."""
    del usage_path
    configure_logging("test")
    gateway = FakeLlmGateway(result=_llm_result())
    client = TestClient(create_app(llm_gateway=gateway))
    token = _assistant_csrf(client)
    capsys.readouterr()

    response = _post_assistant(client, token)
    events = _json_events(capsys.readouterr().out)
    complete = [event for event in events if event.get("event") == "usage.complete"]

    assert response.status_code == 200
    assert complete
    for event in complete:
        assert event["cost_nanos"] != "[REDACTED]"
        assert event["duration_ms"] != "[REDACTED]"
        assert type(event["cost_nanos"]) is int
        assert type(event["duration_ms"]) is int
        assert "intent" not in event
        assert "material" not in event
        assert "output" not in event


def test_current_cost_sums_committed_rows_of_one_request(usage_path: Path) -> None:
    """Проверяет, что цена запроса складывает все committed-строки одного id."""
    now = datetime.now(UTC)
    ledger = UsageLedger(usage_path)
    ledger.record(_committed_event("req-sum", 111, now))
    ledger.record(_committed_event("req-sum", 222, now))
    client = TestClient(create_app())

    payload = client.get("/api/usage", params={"request_id": "req-sum"}).json()
    page = client.get("/usage", params={"request_id": "req-sum"})
    current_block = _section_between(page.text, "Текущий запрос", "Сегодня")

    assert payload["current"] is not None
    assert payload["current"]["cost_nanos"] == 333
    assert payload["current"]["currency"] == "USD"
    assert "333" in current_block
    assert "111" not in current_block
    assert "222" not in current_block
    assert _committed_cost(ledger, "req-sum", "ok") == 333


def test_current_cost_unit_is_nano_usd_not_bare_dollars(usage_path: Path) -> None:
    """Проверяет подпись нано-USD у цены запроса без голого USD."""
    UsageLedger(usage_path).record(
        _committed_event("req-unit", 450, datetime.now(UTC)),
    )
    page = TestClient(create_app()).get("/usage", params={"request_id": "req-unit"})
    current_block = _section_between(page.text, "Текущий запрос", "Сегодня")
    table_head = _section_between(page.text, "<thead>", "</thead>")

    assert "450" in current_block
    assert "нано-USD" in current_block
    assert "450 USD" not in current_block
    assert " USD" not in current_block
    assert "нано-USD" in table_head
    assert ">USD<" not in table_head
    assert ">currency<" not in table_head


def test_null_limit_renders_as_na(usage_path: Path) -> None:
    """Проверяет отображение n/a, когда дневной потолок не задан."""
    del usage_path
    client = TestClient(create_app())
    payload = client.get("/api/usage").json()
    page = client.get("/usage")

    assert payload["today"]["limit_nanos"] is None
    assert "n/a" in page.text


def _committed_event(
    request_id: str,
    cost_nanos: int,
    created_at: datetime,
) -> UsageEvent:
    """Собирает committed-строку без пользовательского текста."""
    return UsageEvent(
        request_id=request_id,
        operation="unspecified",
        skill=None,
        model="deepseek-flash",
        prompt_tokens=1,
        completion_tokens=0,
        total_tokens=1,
        cache_hit=0,
        cache_miss=1,
        reasoning=None,
        cost_nanos=cost_nanos,
        currency="USD",
        tariff_cache_hit=Decimal("0.006"),
        tariff_cache_miss=Decimal("0.30"),
        tariff_output=Decimal("1.20"),
        tariff_source="https://api-docs.deepseek.com/quick_start/pricing",
        tariff_verified_at=date(2026, 9, 11),
        status="committed",
        created_at=created_at,
    )


def _sum_committed_utc(path: Path, utc_day: str) -> int:
    """Считает сумму committed-строк SQLite за указанную UTC-дату."""
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(cost_nanos), 0)
            FROM usage_events
            WHERE status = 'committed'
            AND date(created_at) = ?
            """,
            (utc_day,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def _llm_result() -> LlmResult:
    """Собирает успешный ответ тестового шлюза."""
    return LlmResult(
        text="ok",
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _assistant_csrf(client: TestClient) -> str:
    """Читает CSRF-токен из корневой страницы ассистента."""
    parser = _CsrfParser()
    parser.feed(client.get("/").text)
    assert len(parser.tokens) == 1
    return parser.tokens[0]


def _post_assistant(client: TestClient, token: str) -> Response:
    """Отправляет JSON-запрос ассистента с CSRF в заголовке."""
    return client.post(
        "/api/assistant",
        json={"intent": "Сделай краткое резюме", "material": "короткий текст"},
        headers={
            "origin": "http://testserver",
            "x-csrf-token": token,
        },
    )


def _section_between(html: str, start: str, end: str) -> str:
    """Возвращает фрагмент разметки между двумя маркерами."""
    after_start = html.split(start, 1)[1]
    return after_start.split(end, 1)[0]


def _json_events(output: str) -> list[dict[str, object]]:
    """Разбирает структурированные JSON-строки лога."""
    events: list[dict[str, object]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


class _CsrfParser(HTMLParser):
    """Собирает скрытые CSRF-токены из разметки."""

    def __init__(self) -> None:
        """Готовит пустой список найденных токенов."""
        super().__init__()
        self.tokens: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Запоминает значение поля csrf_token."""
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            token = attributes.get("value")
            if token is not None:
                self.tokens.append(token)
