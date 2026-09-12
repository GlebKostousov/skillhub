"""Проверяет запись расходов после успешного complete()."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from skillhub.assistant import Assistant, PromptSkillHandler, SkillHandlerRegistry
from skillhub.classifier import SkillClassifier
from skillhub.core import bind_request_id, clear_request_context
from skillhub.llm import (
    FakeLlmGateway,
    LlmMessage,
    LlmRequest,
    LlmResult,
    LlmUsage,
    MeteredLlmGateway,
    ProviderError,
)
from skillhub.registry import Skill
from skillhub.usage import bind_call, load_tariffs, open_ledger

_REQUEST_ID = "req-metered-1"
_LEAK_MARKER = "USER-CONTENT-MUST-NOT-PERSIST-e5s2"
_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"


def test_metered_gateway_commits_after_successful_complete(tmp_path: Path) -> None:
    """Проверяет committed-строку после успешного ответа шлюза."""
    path = tmp_path / "usage.sqlite3"
    gateway = _metered(tmp_path, path, prompt_tokens=1_000_000, completion_tokens=0)
    bind_request_id(_REQUEST_ID)
    try:
        gateway.complete(_request())
    finally:
        clear_request_context()

    event = open_ledger(path).list()[0]
    assert event.request_id == _REQUEST_ID
    assert event.operation == "unspecified"
    assert event.skill is None
    assert event.model == "probe-flash"
    assert event.prompt_tokens == 1_000_000
    assert event.completion_tokens == 0
    assert event.total_tokens == 1_000_000
    assert event.cache_hit == 0
    assert event.cache_miss == 1_000_000
    assert event.reasoning is None
    assert event.cost_nanos == 300_000_000
    assert event.currency == "USD"
    assert event.tariff_cache_hit == Decimal("0.006")
    assert event.tariff_cache_miss == Decimal("0.30")
    assert event.tariff_output == Decimal("1.20")
    assert event.tariff_source == _SOURCE
    assert event.tariff_verified_at == date(2026, 9, 11)
    assert event.status == "committed"
    assert event.created_at.tzinfo is not None


def test_metered_gateway_skips_commit_when_inner_fails(tmp_path: Path) -> None:
    """Проверяет отсутствие committed-строки при отказе внутреннего шлюза."""
    path = tmp_path / "usage.sqlite3"
    catalog = load_tariffs(_write_flat_tariff(tmp_path))
    gateway = MeteredLlmGateway(
        FakeLlmGateway(error=ProviderError()),
        open_ledger(path),
        catalog,
    )

    with pytest.raises(ProviderError):
        gateway.complete(_request())

    assert open_ledger(path).list() == ()


def test_yaml_change_does_not_recalculate_stored_cost(tmp_path: Path) -> None:
    """Проверяет, что смена YAML не меняет уже записанные нано-USD."""
    yaml_path = _write_flat_tariff(tmp_path, cache_miss="0.30")
    ledger_path = tmp_path / "usage.sqlite3"
    first = _metered_from_loader(ledger_path, yaml_path, prompt_tokens=1_000_000)
    first.complete(_request())

    _write_flat_tariff(tmp_path, cache_miss="0.15")
    second = _metered_from_loader(ledger_path, yaml_path, prompt_tokens=1_000_000)
    second.complete(_request())

    events = open_ledger(ledger_path).list()
    assert events[0].cost_nanos == 300_000_000
    assert events[1].cost_nanos == 150_000_000
    assert events[0].tariff_cache_miss == Decimal("0.30")


def test_restart_reads_same_committed_rows(tmp_path: Path) -> None:
    """Проверяет, что новый процесс читает те же строки того же файла."""
    path = tmp_path / "usage.sqlite3"
    _metered(tmp_path, path, prompt_tokens=1_000_000, completion_tokens=0).complete(
        _request()
    )
    first = open_ledger(path).list()

    assert open_ledger(path).list() == first


def test_user_content_is_absent_from_sqlite_file(tmp_path: Path) -> None:
    """Проверяет отсутствие пользовательского текста в файле журнала."""
    path = tmp_path / "usage.sqlite3"
    gateway = _metered(tmp_path, path, prompt_tokens=4, completion_tokens=6)
    gateway.complete(
        LlmRequest(messages=(LlmMessage(role="user", content=_LEAK_MARKER),))
    )

    raw = path.read_bytes()
    assert _LEAK_MARKER.encode() not in raw


def test_bound_generate_records_operation_and_skill(tmp_path: Path) -> None:
    """Проверяет запись generate и режима из контекста usage."""
    path = tmp_path / "usage.sqlite3"
    gateway = _metered(tmp_path, path, prompt_tokens=4, completion_tokens=6)
    with bind_call(operation="generate", skill="text-summary"):
        gateway.complete(_request())

    event = open_ledger(path).list()[0]
    assert event.operation == "generate"
    assert event.skill == "text-summary"


def test_assistant_binds_classify_then_generate_and_resets(tmp_path: Path) -> None:
    """Проверяет classify до select, generate со скиллом и сброс после run."""
    path = tmp_path / "usage.sqlite3"
    catalog = load_tariffs(_write_flat_tariff(tmp_path))
    ledger = open_ledger(path)
    classify = MeteredLlmGateway(
        FakeLlmGateway(result=_result('{"skill": "text-summary"}', 3, 1)),
        ledger,
        catalog,
    )
    generate = MeteredLlmGateway(
        FakeLlmGateway(result=_result("готовый текст", 8, 2)),
        ledger,
        catalog,
    )
    assistant = Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(PromptSkillHandler(generate)),
    )
    bind_request_id(_REQUEST_ID)
    try:
        assistant.run("суммируй", _LEAK_MARKER, {"text-summary": _skill()})
        generate.complete(_request())
    finally:
        clear_request_context()

    events = ledger.list()
    assert [event.operation for event in events] == [
        "classify",
        "generate",
        "unspecified",
    ]
    assert events[0].skill is None
    assert events[1].skill == "text-summary"
    assert events[2].skill is None
    assert _LEAK_MARKER.encode() not in path.read_bytes()


def _metered(
    tmp_path: Path,
    ledger_path: Path,
    *,
    prompt_tokens: int,
    completion_tokens: int,
) -> MeteredLlmGateway:
    """Собирает обёртку с плоским тарифом и тестовым шлюзом."""
    return MeteredLlmGateway(
        FakeLlmGateway(result=_result("ответ", prompt_tokens, completion_tokens)),
        open_ledger(ledger_path),
        load_tariffs(_write_flat_tariff(tmp_path)),
    )


def _metered_from_loader(
    ledger_path: Path,
    yaml_path: Path,
    *,
    prompt_tokens: int,
) -> MeteredLlmGateway:
    """Собирает обёртку, которая перечитывает YAML при каждом complete()."""
    return MeteredLlmGateway(
        FakeLlmGateway(result=_result("ответ", prompt_tokens, 0)),
        open_ledger(ledger_path),
        lambda: load_tariffs(yaml_path),
    )


def _request() -> LlmRequest:
    """Собирает запрос без секрета для обычных проверок."""
    return LlmRequest(messages=(LlmMessage(role="user", content="кратко"),))


def _result(text: str, prompt_tokens: int, completion_tokens: int) -> LlmResult:
    """Собирает успешный ответ с фиксированными токенами."""
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def _skill() -> Skill:
    """Собирает скилл снимка для оркестрации."""
    return Skill(
        name="text-summary",
        caption="Кратко",
        description="Краткое изложение",
        body="Суммируй текст.",
        has_files=False,
    )


def _write_flat_tariff(tmp_path: Path, *, cache_miss: str = "0.30") -> Path:
    """Пишет тариф с одинаковыми пиковыми и внепиковыми ценами."""
    path = tmp_path / "model-tariffs.yaml"
    payload = {
        "version": 1,
        "currency": "USD",
        "unit": "per_million_tokens",
        "effective_from": "2026-09-11",
        "source_url": _SOURCE,
        "verified_at": "2026-09-11",
        "models": [
            {
                "model": "probe-flash",
                "max_tokens": 4096,
                "timeout": 30,
                "temperature": 0,
                "peak": {
                    "cache_hit": "0.006",
                    "cache_miss": cache_miss,
                    "output": "1.20",
                },
                "off_peak": {
                    "cache_hit": "0.006",
                    "cache_miss": cache_miss,
                    "output": "1.20",
                },
                "peak_windows": {
                    "days": ["Monday"],
                    "intervals": [{"start": "01:00", "end": "04:00"}],
                },
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path
