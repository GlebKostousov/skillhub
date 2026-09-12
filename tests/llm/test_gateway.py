"""Проверяет публичный фасад шлюза модели."""

import json
from dataclasses import dataclass

import httpx
import pytest

from skillhub.llm import (
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    DeepSeekLlmGateway,
    FakeLlmGateway,
    GenerationUnavailableError,
    LlmGateway,
    LlmMessage,
    LlmRequest,
    LlmResult,
    LlmUsage,
    ProviderError,
)

_PROVIDER_VALUE = "sk-test-provider-value-6418"
_PROMPT = "Суммируй закрытый текст пользователя."
_ANSWER = "Краткий ответ модели."


def _request() -> LlmRequest:
    """Собирает типовой запрос фасада."""
    return LlmRequest(messages=(LlmMessage(role="user", content=_PROMPT),))


def _success_payload(
    *,
    text: str = _ANSWER,
    finish_reason: str = "stop",
) -> dict[str, object]:
    """Собирает успешный JSON DeepSeek с фиксированным usage."""
    return {
        "choices": [
            {
                "message": {"content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 4,
            "completion_tokens": 6,
            "total_tokens": 10,
        },
    }


def _unused_client() -> httpx.Client:
    """Собирает клиент, который не должен получить запрос."""
    return httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        timeout=1.0,
        follow_redirects=False,
    )


def _gateway_for(
    handler: httpx.MockTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> DeepSeekLlmGateway:
    """Собирает адаптер с подставленным транспортом."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    return DeepSeekLlmGateway(
        client=httpx.Client(transport=handler, timeout=1.0, follow_redirects=False)
    )


def test_fake_gateway_returns_successful_result() -> None:
    """Проверяет успешный типизированный ответ тестового шва."""
    expected = LlmResult(
        text=_ANSWER,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=4, completion_tokens=6, total_tokens=10),
    )
    gateway = FakeLlmGateway(result=expected)

    result = gateway.complete(_request())

    assert result == expected
    assert gateway.requests == [_request()]


def test_fake_gateway_without_result_is_generation_unavailable() -> None:
    """Проверяет отказ тестового шва без заранее заданного ответа."""
    gateway = FakeLlmGateway()

    with pytest.raises(GenerationUnavailableError):
        gateway.complete(_request())


def test_fake_gateway_raises_configured_provider_error() -> None:
    """Проверяет типизированный отказ тестового шва."""
    gateway = FakeLlmGateway(error=ProviderError())

    with pytest.raises(ProviderError) as captured:
        gateway.complete(_request())

    assert captured.value.code == "provider_error"
    assert _PROMPT not in str(captured.value)


def test_gateway_constructs_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет создание производственного шлюза без ключа."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    gateway = DeepSeekLlmGateway()

    assert isinstance(gateway, LlmGateway)


def test_missing_key_is_generation_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ без ключа поставщика."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    gateway = DeepSeekLlmGateway(client=_unused_client())

    with pytest.raises(GenerationUnavailableError) as captured:
        gateway.complete(_request())

    assert captured.value.code == "generation_unavailable"
    assert _PROMPT not in str(captured.value)


def test_blank_key_is_generation_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ при пустом ключе поставщика."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
    gateway = DeepSeekLlmGateway(client=_unused_client())

    with pytest.raises(GenerationUnavailableError):
        gateway.complete(_request())


def test_successful_provider_response_becomes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет успешный ответ поставщика на публичном шве."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_success_payload())

    result = _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(
        _request()
    )

    assert result == LlmResult(
        text=_ANSWER,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=4, completion_tokens=6, total_tokens=10),
    )
    assert len(seen) == 1
    assert str(seen[0].url) == DEEPSEEK_API_URL
    payload = json.loads(seen[0].content)
    assert payload["model"] == DEEPSEEK_MODEL
    assert payload["temperature"] == 0


def test_timeout_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет типизированный отказ при таймауте поставщика."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        timeout_text = "timeout"
        raise httpx.TimeoutException(timeout_text)

    with pytest.raises(ProviderError) as captured:
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(_request())

    assert captured.value.code == "provider_error"
    assert len(seen) == 1
    assert _PROMPT not in str(captured.value)


def test_http_error_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет типизированный отказ при HTTP-ошибке поставщика."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=_PROMPT)

    with pytest.raises(ProviderError) as captured:
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(_request())

    assert captured.value.code == "provider_error"
    assert _PROMPT not in str(captured.value)


def test_invalid_json_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет типизированный отказ при невалидном JSON."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{not-json")

    with pytest.raises(ProviderError):
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(_request())


@pytest.mark.parametrize(
    "payload",
    [
        ["not-an-object"],
        {"choices": []},
        {"choices": [{"message": {"content": _ANSWER}}]},
        {
            "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
        {"choices": [{"message": {"content": _ANSWER}, "finish_reason": "stop"}]},
        {
            "choices": [{"message": {"content": _ANSWER}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": "1",
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
    ],
)
def test_invalid_payload_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Проверяет отказ при структурно неверном ответе поставщика."""

    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    with pytest.raises(ProviderError):
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(_request())


def test_non_stop_finish_reason_becomes_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ при причине завершения, отличной от stop."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(finish_reason="length"))

    with pytest.raises(ProviderError) as captured:
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(_request())

    assert captured.value.code == "provider_error"


def test_request_rejects_user_supplied_url() -> None:
    """Проверяет отсутствие пользовательского URL в контракте запроса."""
    with pytest.raises(TypeError):
        LlmRequest(  # type: ignore[call-arg]
            messages=(LlmMessage(role="user", content=_PROMPT),),
            url="https://evil.example/ssrf",
        )
    assert "url" not in LlmRequest.__dataclass_fields__
    assert "model" not in LlmRequest.__dataclass_fields__


def test_complete_rejects_forged_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отказ при подмене адреса поставщика в запросе."""

    @dataclass(frozen=True, slots=True)
    class ForgedRequest:
        """Поддельный запрос с пользовательским URL."""

        messages: tuple[LlmMessage, ...]
        url: str

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload())

    with pytest.raises(ProviderError):
        _gateway_for(httpx.MockTransport(handler), monkeypatch).complete(
            ForgedRequest(  # type: ignore[arg-type]
                messages=_request().messages,
                url="https://evil.example/ssrf",
            )
        )
