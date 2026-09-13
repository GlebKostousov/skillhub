"""Проверяет отказ вызова при превышении дневного лимита."""

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from skillhub.llm import (
    DEEPSEEK_MODEL,
    FakeLlmGateway,
    GenerationUnavailableError,
    LlmGateway,
    LlmMessage,
    LlmRequest,
    LlmResult,
    LlmUsage,
    MeteredLlmGateway,
    ProviderError,
)
from skillhub.runtime import RuntimeStore
from skillhub.usage import (
    DailyBudgetExceededError,
    LedgerError,
    UsageEvent,
    bind_call,
    load_tariffs,
    open_ledger,
)

_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_RESERVE_NANOS = 4_915_500


def test_complete_rejects_over_budget_before_inner(tmp_path: Path) -> None:
    """Проверяет 429 до вызова inner, если worst-case резерв не помещается."""
    inner = FakeLlmGateway(result=_result("ответ", 4, 6))
    gateway = _metered(
        tmp_path,
        inner,
        daily_budget_nanos=0,
        yaml_path=_write_tariff(
            tmp_path,
            cache_miss="0.0001",
            output="0.0001",
            max_tokens=1,
        ),
    )

    with pytest.raises(DailyBudgetExceededError) as captured:
        gateway.complete(_request("x"))

    assert captured.value.code == "daily_budget_exceeded"
    assert captured.value.status_code == 429
    assert captured.value.public_message == "Дневной лимит модельных расходов исчерпан"
    assert inner.requests == []
    assert open_ledger(tmp_path / "usage.sqlite3").list() == ()


def test_success_commits_actual_tokens_not_reserve(tmp_path: Path) -> None:
    """Проверяет фактические токены при стоимости не выше дневного лимита."""
    inner = FakeLlmGateway(result=_result("ответ", 1_000_000, 0))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS)

    gateway.complete(_request("x"))

    event = open_ledger(tmp_path / "usage.sqlite3").list()[0]
    assert event.status == "committed"
    assert event.prompt_tokens == 1_000_000
    assert event.completion_tokens == 0
    assert event.cost_nanos <= _RESERVE_NANOS
    assert inner.requests != []


def test_huge_actual_usage_keeps_held_within_budget(tmp_path: Path) -> None:
    """Проверяет, что огромный usage поставщика не поднимает held выше лимита."""
    path = tmp_path / "usage.sqlite3"
    inner = FakeLlmGateway(result=_result("ответ", 1_000_000, 0))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS)

    gateway.complete(_request("x"))

    ledger = open_ledger(path)
    committed, reserved = ledger.day_totals(datetime.now(UTC).date())
    event = ledger.list()[0]
    assert reserved == 0
    assert committed <= _RESERVE_NANOS
    assert event.prompt_tokens == 1_000_000
    assert event.completion_tokens == 0


def test_negative_provider_tokens_are_rejected(tmp_path: Path) -> None:
    """Проверяет отказ отрицательных токенов поставщика без снижения SUM."""
    path = tmp_path / "usage.sqlite3"
    inner = FakeLlmGateway(result=_result("ответ", -1, 0))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS)

    with pytest.raises(LedgerError):
        gateway.complete(_request("x"))

    committed, reserved = open_ledger(path).day_totals(datetime.now(UTC).date())
    assert committed == 0
    assert reserved == 0


def test_runtime_error_releases_reserve(tmp_path: Path) -> None:
    """Проверяет, что RuntimeError после reserve не оставляет held."""
    path = tmp_path / "usage.sqlite3"
    catalog = load_tariffs(_write_tariff(tmp_path))
    ledger = open_ledger(path)
    failing = MeteredLlmGateway(
        _BrokenGateway(),
        ledger,
        catalog,
        daily_budget_nanos=_RESERVE_NANOS,
    )

    with pytest.raises(RuntimeError):
        failing.complete(_request("x"))

    committed, reserved = ledger.day_totals(datetime.now(UTC).date())
    assert committed == 0
    assert reserved == 0


def test_provider_error_releases_reserve_for_next_call(tmp_path: Path) -> None:
    """Проверяет, что отказ поставщика возвращает лимит следующему вызову."""
    path = tmp_path / "usage.sqlite3"
    catalog = load_tariffs(_write_tariff(tmp_path))
    ledger = open_ledger(path)
    failing = MeteredLlmGateway(
        FakeLlmGateway(error=ProviderError()),
        ledger,
        catalog,
        daily_budget_nanos=_RESERVE_NANOS,
    )
    with pytest.raises(ProviderError):
        failing.complete(_request("x"))
    assert ledger.list() == ()

    succeeding = MeteredLlmGateway(
        FakeLlmGateway(result=_result("ответ", 4, 6)),
        ledger,
        catalog,
        daily_budget_nanos=_RESERVE_NANOS,
    )
    succeeding.complete(_request("x"))

    assert ledger.list()[0].status == "committed"


def test_generation_unavailable_releases_reserve(tmp_path: Path) -> None:
    """Проверяет возврат лимита при недоступности генерации."""
    path = tmp_path / "usage.sqlite3"
    catalog = load_tariffs(_write_tariff(tmp_path))
    ledger = open_ledger(path)
    failing = MeteredLlmGateway(
        FakeLlmGateway(),
        ledger,
        catalog,
        daily_budget_nanos=_RESERVE_NANOS,
    )
    with pytest.raises(GenerationUnavailableError):
        failing.complete(_request("x"))

    succeeding = MeteredLlmGateway(
        FakeLlmGateway(result=_result("ответ", 4, 6)),
        ledger,
        catalog,
        daily_budget_nanos=_RESERVE_NANOS,
    )
    succeeding.complete(_request("x"))

    assert ledger.list()[0].status == "committed"


def test_classify_and_generate_are_separate_rows(tmp_path: Path) -> None:
    """Проверяет разные строки журнала для classify и generate."""
    inner = FakeLlmGateway(result=_result("ответ", 4, 6))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS * 2)

    with bind_call(operation="classify"):
        gateway.complete(_request("x"))
    with bind_call(operation="generate", skill="text-summary"):
        gateway.complete(_request("x"))

    events = open_ledger(tmp_path / "usage.sqlite3").list()
    assert [event.operation for event in events] == ["classify", "generate"]
    assert events[0].skill is None
    assert events[1].skill == "text-summary"


def test_reserved_row_blocks_parallel_complete(tmp_path: Path) -> None:
    """Проверяет, что удерживаемый резерв не даёт второму вызову обойти лимит."""
    inner = _HoldGateway(_result("ответ", 4, 6))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS)
    worker = threading.Thread(target=gateway.complete, args=(_request("x"),))
    worker.start()
    assert inner.held.wait(timeout=5)

    with pytest.raises(DailyBudgetExceededError):
        gateway.complete(_request("x"))

    inner.release.set()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert len(open_ledger(tmp_path / "usage.sqlite3").list()) == 1


def test_parallel_threads_do_not_both_commit(tmp_path: Path) -> None:
    """Проверяет, что два одновременных вызова не проходят в один лимит."""
    inner = _SlowGateway(_result("ответ", 4, 6))
    gateway = _metered(tmp_path, inner, daily_budget_nanos=_RESERVE_NANOS)
    request = _request("x")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(gateway.complete, request) for _ in range(2)]
    outcomes = [_outcome(future) for future in futures]

    assert outcomes.count("ok") == 1
    assert outcomes.count("limit") == 1
    assert len(open_ledger(tmp_path / "usage.sqlite3").list()) == 1


def test_committed_event_uses_gateway_model_not_first_catalog(tmp_path: Path) -> None:
    """Проверяет тариф модели шлюза, а не первой строки каталога."""
    inner = FakeLlmGateway(result=_result("ответ", 1_000_000, 0))
    catalog = load_tariffs(_write_two_models(tmp_path))
    gateway = MeteredLlmGateway(
        inner,
        open_ledger(tmp_path / "usage.sqlite3"),
        catalog,
        daily_budget_nanos=10_000_000_000,
    )

    gateway.complete(_request("x"))

    event = open_ledger(tmp_path / "usage.sqlite3").list()[0]
    assert event.model == DEEPSEEK_MODEL
    assert event.cost_nanos == 300_000_000
    assert event.tariff_cache_miss == Decimal("0.30")


def test_yesterday_committed_does_not_consume_today(tmp_path: Path) -> None:
    """Проверяет, что вчерашняя committed-строка не занимает сегодняшний лимит."""
    path = tmp_path / "usage.sqlite3"
    ledger = open_ledger(path)
    ledger.record(_committed_yesterday())
    inner = FakeLlmGateway(result=_result("ответ", 4, 6))
    gateway = MeteredLlmGateway(
        inner,
        ledger,
        load_tariffs(_write_tariff(tmp_path)),
        daily_budget_nanos=_RESERVE_NANOS,
    )

    gateway.complete(_request("x"))

    assert len(ledger.list()) == 2


def test_all_message_code_points_count_in_reserve(tmp_path: Path) -> None:
    """Проверяет сумму code points всех сообщений в worst-case резерве."""
    inner = FakeLlmGateway(result=_result("ответ", 4, 6))
    gateway = _metered(
        tmp_path,
        inner,
        daily_budget_nanos=300,
        yaml_path=_write_tariff(
            tmp_path,
            cache_miss="0.30",
            output="0",
            max_tokens=1,
        ),
    )
    request = LlmRequest(
        messages=(
            LlmMessage(role="user", content="x"),
            LlmMessage(role="user", content="y"),
        )
    )

    with pytest.raises(DailyBudgetExceededError):
        gateway.complete(request)

    assert inner.requests == []


def test_overlay_budget_model_and_max_tokens_apply_on_same_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет смену модели, потолка и max_tokens без нового шлюза."""
    monkeypatch.chdir(tmp_path)
    catalog = load_tariffs(_write_two_models(tmp_path))
    store = RuntimeStore(
        {item.model: item.max_tokens for item in catalog.models},
        daily_budget_nanos=2000,
    )
    inner = FakeLlmGateway(result=_result("ответ", 4, 6))
    gateway = MeteredLlmGateway(
        inner,
        open_ledger(tmp_path / "usage.sqlite3"),
        catalog,
        store=store,
    )

    with pytest.raises(DailyBudgetExceededError):
        gateway.complete(_request("x"))
    store.save(
        _overlay_payload(
            model=DEEPSEEK_MODEL,
            max_tokens=1,
            daily_budget_nanos=2000,
        )
    )
    gateway.complete(_request("x"))
    store.save(_overlay_payload(model="probe-flash", daily_budget_nanos=0))
    with pytest.raises(DailyBudgetExceededError):
        gateway.complete(_request("x"))

    event = open_ledger(tmp_path / "usage.sqlite3").list()[0]
    assert event.model == DEEPSEEK_MODEL
    assert event.status == "committed"
    assert inner.requests == [_request("x")]


def test_complete_keeps_entry_snapshot_if_overlay_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что reserve и commit держат один снимок на весь вызов."""
    monkeypatch.chdir(tmp_path)
    catalog = load_tariffs(_write_two_models(tmp_path))
    store = RuntimeStore(
        {item.model: item.max_tokens for item in catalog.models},
        daily_budget_nanos=10_000_000_000,
    )
    store.save(_overlay_payload(model="probe-flash"))
    inner = _OverlaySwapGateway(store, _result("ответ", 4, 6))
    gateway = MeteredLlmGateway(
        inner,
        open_ledger(tmp_path / "usage.sqlite3"),
        catalog,
        store=store,
    )

    gateway.complete(_request("x"))

    event = open_ledger(tmp_path / "usage.sqlite3").list()[0]
    assert event.model == "probe-flash"
    assert store.snapshot().values.model == DEEPSEEK_MODEL


class _OverlaySwapGateway(LlmGateway):
    """Меняет overlay после входа в complete() внутреннего шлюза."""

    def __init__(self, store: RuntimeStore, result: LlmResult) -> None:
        self._store = store
        self._result = result

    def complete(self, request: LlmRequest) -> LlmResult:
        """Пишет чужую модель в overlay и возвращает успешный результат."""
        del request
        self._store.save(_overlay_payload(model=DEEPSEEK_MODEL, daily_budget_nanos=1))
        return self._result


class _BrokenGateway(LlmGateway):
    """Поднимает RuntimeError после входа в complete()."""

    def complete(self, request: LlmRequest) -> LlmResult:
        """Имитирует сбой внутреннего шлюза после резерва."""
        del request
        raise RuntimeError


class _HoldGateway(LlmGateway):
    """Задерживает complete(), пока тест держит резерв."""

    def __init__(self, result: LlmResult) -> None:
        self.requests: list[LlmRequest] = []
        self.held = threading.Event()
        self.release = threading.Event()
        self._result = result

    def complete(self, request: LlmRequest) -> LlmResult:
        """Удерживает вызов после входа, затем возвращает результат."""
        self.requests.append(request)
        self.held.set()
        assert self.release.wait(timeout=5)
        return self._result


class _SlowGateway(LlmGateway):
    """Даёт окно, в котором второй поток успевает запросить резерв."""

    def __init__(self, result: LlmResult) -> None:
        self._result = result
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResult:
        """Ждёт короткую паузу и возвращает успешный результат."""
        self.requests.append(request)
        time.sleep(0.2)
        return self._result


def _outcome(future: Future[LlmResult]) -> str:
    """Возвращает исход параллельного complete()."""
    try:
        future.result()
    except DailyBudgetExceededError:
        return "limit"
    return "ok"


def _metered(
    tmp_path: Path,
    inner: LlmGateway,
    *,
    daily_budget_nanos: int | None,
    yaml_path: Path | None = None,
) -> MeteredLlmGateway:
    """Собирает обёртку с дневным потолком и тестовым тарифом."""
    return MeteredLlmGateway(
        inner,
        open_ledger(tmp_path / "usage.sqlite3"),
        load_tariffs(yaml_path or _write_tariff(tmp_path)),
        daily_budget_nanos=daily_budget_nanos,
    )


def _request(content: str) -> LlmRequest:
    """Собирает запрос с заданным текстом сообщения."""
    return LlmRequest(messages=(LlmMessage(role="user", content=content),))


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


def _committed_yesterday() -> UsageEvent:
    """Собирает вчерашнюю committed-строку с большой стоимостью."""
    return UsageEvent(
        request_id="req-yesterday",
        operation="unspecified",
        skill=None,
        model="probe-flash",
        prompt_tokens=1,
        completion_tokens=0,
        total_tokens=1,
        cache_hit=0,
        cache_miss=1,
        reasoning=None,
        cost_nanos=9_000_000_000,
        currency="USD",
        tariff_cache_hit=Decimal("0.006"),
        tariff_cache_miss=Decimal("0.30"),
        tariff_output=Decimal("1.20"),
        tariff_source=_SOURCE,
        tariff_verified_at=datetime.now(UTC).date(),
        status="committed",
        created_at=datetime.now(UTC) - timedelta(days=1),
    )


def _write_tariff(
    tmp_path: Path,
    *,
    cache_miss: str = "0.30",
    output: str = "1.20",
    max_tokens: int = 4096,
    model: str = "probe-flash",
) -> Path:
    """Пишет тариф с известными пиковыми ценами для расчёта резерва."""
    path = tmp_path / "model-tariffs.yaml"
    path.write_text(
        yaml.safe_dump(
            _tariff_payload(model, cache_miss, output, max_tokens),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_two_models(tmp_path: Path) -> Path:
    """Пишет каталог, где первая модель дороже модели шлюза."""
    path = tmp_path / "model-tariffs.yaml"
    payload = {
        "version": 1,
        "currency": "USD",
        "unit": "per_million_tokens",
        "effective_from": "2026-09-11",
        "source_url": _SOURCE,
        "verified_at": "2026-09-11",
        "models": [
            _model_entry("probe-flash", "0.99", "9.00", 4096),
            _model_entry(DEEPSEEK_MODEL, "0.30", "1.20", 4096),
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _tariff_payload(
    model: str,
    cache_miss: str,
    output: str,
    max_tokens: int,
) -> dict[str, object]:
    """Собирает корневой документ тарифа одной модели."""
    return {
        "version": 1,
        "currency": "USD",
        "unit": "per_million_tokens",
        "effective_from": "2026-09-11",
        "source_url": _SOURCE,
        "verified_at": "2026-09-11",
        "models": [_model_entry(model, cache_miss, output, max_tokens)],
    }


def _overlay_payload(**overrides: object) -> dict[str, object]:
    """Собирает полный документ overlay для смены снимка в тесте."""
    values: dict[str, object] = {
        "model": "probe-flash",
        "max_tokens": 4096,
        "temperature": 0,
        "timeout": 60.0,
        "stream": False,
        "thinking": "enabled",
        "reasoning_effort": "high",
        "top_p": 1.0,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "stop": [],
        "response_format": "text",
        "daily_budget_nanos": 10_000_000_000,
    }
    values.update(overrides)
    return values


def _model_entry(
    model: str,
    cache_miss: str,
    output: str,
    max_tokens: int,
) -> dict[str, object]:
    """Собирает одну модель тарифного YAML."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "timeout": 30,
        "temperature": 0,
        "peak": {
            "cache_hit": "0.006",
            "cache_miss": cache_miss,
            "output": output,
        },
        "off_peak": {
            "cache_hit": "0.006",
            "cache_miss": cache_miss,
            "output": output,
        },
        "peak_windows": {
            "days": ["Monday"],
            "intervals": [{"start": "01:00", "end": "04:00"}],
        },
    }
