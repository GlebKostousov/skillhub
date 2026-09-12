"""Проверяет, что следующий complete() берёт knobs из снимка RuntimeStore."""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from skillhub.llm import (
    DEEPSEEK_API_URL,
    DeepSeekLlmGateway,
    LlmGateway,
    LlmMessage,
    LlmRequest,
    LlmResult,
    MeteredLlmGateway,
)
from skillhub.runtime import OverlayValues, RuntimeStore
from skillhub.usage import load_tariffs, open_ledger

_PROVIDER_VALUE = "sk-test-hot-apply-provider-6418"
_PROMPT = "Суммируй закрытый текст пользователя."
_ANSWER = "Краткий ответ модели."
_LIMITS = {"deepseek-flash": 16384, "deepseek-reasoner": 8192}


def test_complete_uses_new_overlay_knobs_on_same_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет смену max_tokens, модели и thinking без нового шлюза."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    seen: list[dict[str, object]] = []
    store = RuntimeStore(_LIMITS)
    gateway = DeepSeekLlmGateway(client=_client(seen), store=store)

    gateway.complete(_request())
    store.save(
        _payload(
            model="deepseek-reasoner",
            max_tokens=2048,
            thinking="disabled",
            reasoning_effort="low",
            top_p=0.8,
            frequency_penalty=0.1,
            presence_penalty=0.2,
            stop=["END"],
            response_format="json_object",
        )
    )
    gateway.complete(_request())

    first, second = seen
    assert first["model"] == "deepseek-flash"
    assert first["max_tokens"] == 16384
    assert "thinking" not in first
    assert second["model"] == "deepseek-reasoner"
    assert second["max_tokens"] == 2048
    assert second["thinking"] == {"type": "disabled"}
    assert second["reasoning_effort"] == "low"
    assert second["top_p"] == 0.8
    assert second["frequency_penalty"] == 0.1
    assert second["presence_penalty"] == 0.2
    assert second["stop"] == ["END"]
    assert second["response_format"] == {"type": "json_object"}
    assert second["stream"] is False


def test_timeout_is_passed_to_post_not_client_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что timeout снимка уходит в post(), а не в конструктор клиента."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return _success(request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=1.0,
        follow_redirects=False,
    )
    store = RuntimeStore(_LIMITS)
    gateway = DeepSeekLlmGateway(client=client, store=store)

    gateway.complete(_request())
    store.save(_payload(timeout=12.0))
    gateway.complete(_request())

    assert client.timeout == httpx.Timeout(1.0)
    assert seen == [
        {"connect": 60.0, "pool": 60.0, "read": 60.0, "write": 60.0},
        {"connect": 12.0, "pool": 12.0, "read": 12.0, "write": 12.0},
    ]


def test_thinking_enabled_omits_field_from_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что включённое thinking не попадает в тело запроса."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success(request)

    store = RuntimeStore(_LIMITS)
    store.save(_payload(thinking="enabled"))
    gateway = DeepSeekLlmGateway(
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=1.0,
            follow_redirects=False,
        ),
        store=store,
    )

    gateway.complete(_request())

    payload = json.loads(seen[0].content)
    assert "thinking" not in payload
    assert payload["stream"] is False
    assert str(seen[0].url) == DEEPSEEK_API_URL


def test_save_after_reserve_cannot_change_in_flight_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что save() между reserve и post не меняет тело запроса."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    seen: list[dict[str, object]] = []
    store = RuntimeStore(_LIMITS)
    store.save(_payload(max_tokens=2048))
    deepseek = DeepSeekLlmGateway(client=_client(seen), store=store)
    gateway = MeteredLlmGateway(
        _SaveThenDeepSeek(store, deepseek),
        open_ledger(tmp_path / "usage.sqlite3"),
        load_tariffs(_write_tariff(tmp_path)),
        store=store,
    )

    gateway.complete(_request())

    assert seen[0]["model"] == "deepseek-flash"
    assert seen[0]["max_tokens"] == 2048
    assert store.snapshot().values.max_tokens == 1


def test_provider_max_tokens_matches_tariff_clamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что тело шлёт тот же потолок max_tokens, что и reserve."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    seen: list[dict[str, object]] = []
    catalog = load_tariffs(_write_tariff(tmp_path, max_tokens=4096))
    store = RuntimeStore({"deepseek-flash": 4096})
    gateway = MeteredLlmGateway(
        DeepSeekLlmGateway(client=_client(seen), store=store),
        open_ledger(tmp_path / "usage.sqlite3"),
        catalog,
        store=store,
    )

    gateway.complete(_request())

    assert store.snapshot().values.max_tokens == 16384
    assert seen[0]["max_tokens"] == 4096


class _SaveThenDeepSeek(LlmGateway):
    """Пишет overlay после reserve и проксирует вызов DeepSeek."""

    def __init__(self, store: RuntimeStore, inner: DeepSeekLlmGateway) -> None:
        self._store = store
        self._inner = inner

    def complete(self, request: LlmRequest) -> LlmResult:
        """Меняет overlay и вызывает повторный снимок внутреннего шлюза."""
        self._store.save(_payload(max_tokens=1))
        return self._inner.complete(request)

    def complete_with_values(
        self,
        request: LlmRequest,
        values: OverlayValues,
    ) -> LlmResult:
        """Меняет overlay и отдаёт тот же снимок во внутренний шлюз."""
        self._store.save(_payload(max_tokens=1))
        return self._inner.complete_with_values(request, values)


def _write_tariff(tmp_path: Path, *, max_tokens: int = 4096) -> Path:
    """Пишет тариф deepseek-flash с заданным потолком max_tokens."""
    path = tmp_path / "model-tariffs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "currency": "USD",
                "unit": "per_million_tokens",
                "effective_from": "2026-09-11",
                "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
                "verified_at": "2026-09-11",
                "models": [
                    {
                        "model": "deepseek-flash",
                        "max_tokens": max_tokens,
                        "timeout": 30,
                        "temperature": 0,
                        "peak": {
                            "cache_hit": "0.006",
                            "cache_miss": "0.30",
                            "output": "1.20",
                        },
                        "off_peak": {
                            "cache_hit": "0.006",
                            "cache_miss": "0.30",
                            "output": "1.20",
                        },
                        "peak_windows": {
                            "days": ["Monday"],
                            "intervals": [{"start": "01:00", "end": "04:00"}],
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _success(_request: httpx.Request) -> httpx.Response:
    """Возвращает успешный JSON DeepSeek с фиксированным usage."""
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": _ANSWER}, "finish_reason": "stop"},
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "total_tokens": 10,
            },
        },
    )


def _client(seen: list[dict[str, object]]) -> httpx.Client:
    """Собирает клиент, который запоминает тело каждого запроса."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _success(request)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=1.0,
        follow_redirects=False,
    )


def _request() -> LlmRequest:
    """Собирает типовой запрос фасада."""
    return LlmRequest(messages=(LlmMessage(role="user", content=_PROMPT),))


def _payload(**overrides: object) -> dict[str, object]:
    """Собирает полный документ overlay для смены снимка в тесте."""
    values: dict[str, object] = {
        "model": "deepseek-flash",
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
        "daily_budget_nanos": None,
    }
    values.update(overrides)
    return values
