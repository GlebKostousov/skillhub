"""Проверяет отсутствие ключа и содержимого в отказах шлюза."""

import json

import httpx
import pytest
from structlog.testing import capture_logs

from skillhub.core import configure_logging
from skillhub.llm import DeepSeekLlmGateway, LlmMessage, LlmRequest, ProviderError

_PROVIDER_VALUE = "sk-redaction-provider-value-9172"
_CONTENT = "закрытый пользовательский текст для модели"


def _request() -> LlmRequest:
    """Собирает запрос с чувствительным содержимым."""
    return LlmRequest(messages=(LlmMessage(role="user", content=_CONTENT),))


def _gateway(
    handler: httpx.MockTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> DeepSeekLlmGateway:
    """Собирает адаптер с ключом и подставленным транспортом."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PROVIDER_VALUE)
    return DeepSeekLlmGateway(
        client=httpx.Client(transport=handler, timeout=1.0, follow_redirects=False)
    )


def test_provider_error_redacts_key_and_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет отсутствие ключа и content в логах и исключении."""
    configure_logging("test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, text=f"upstream failed: {_CONTENT} {_PROVIDER_VALUE}"
        )

    with capture_logs() as logs, pytest.raises(ProviderError) as captured:
        _gateway(httpx.MockTransport(handler), monkeypatch).complete(_request())

    rendered = capsys.readouterr().out
    assert captured.value.code == "provider_error"
    assert _PROVIDER_VALUE not in str(captured.value)
    assert _CONTENT not in str(captured.value)
    assert _PROVIDER_VALUE not in rendered
    assert _CONTENT not in rendered
    for event in logs:
        serialized = json.dumps(event, ensure_ascii=True)
        assert _PROVIDER_VALUE not in serialized
        assert _CONTENT not in serialized
        assert event.get("error_code") == "provider_error"


def test_timeout_error_redacts_key_and_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отсутствие ключа и content при таймауте."""

    def handler(_request: httpx.Request) -> httpx.Response:
        timeout_text = _CONTENT
        raise httpx.TimeoutException(timeout_text)

    with capture_logs() as logs, pytest.raises(ProviderError) as captured:
        _gateway(httpx.MockTransport(handler), monkeypatch).complete(_request())

    assert _PROVIDER_VALUE not in str(captured.value)
    assert _CONTENT not in str(captured.value)
    for event in logs:
        serialized = json.dumps(event, ensure_ascii=True)
        assert _PROVIDER_VALUE not in serialized
        assert _CONTENT not in serialized
