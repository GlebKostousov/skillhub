"""Вызывает DeepSeek через httpx без пользовательского URL."""

import json
import os
from typing import Any, cast

import httpx
import structlog

from skillhub.llm._constants import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    HTTP_OK,
    MAX_OUTPUT_TOKENS,
    REQUEST_TIMEOUT_SECONDS,
    TEMPERATURE,
)
from skillhub.llm._errors import GenerationUnavailableError, ProviderError
from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult, LlmUsage


class DeepSeekLlmGateway(LlmGateway):
    """Отправляет один запрос к фиксированному адресу DeepSeek."""

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        """Сохраняет HTTP-клиент без чтения ключа.

        Args:
            client: готовый клиент или штатный клиент пакета.
        """
        self._client = client or httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        )

    def complete(self, request: LlmRequest) -> LlmResult:
        """Выполняет один вызов поставщика.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат с текстом, причиной и usage.
        """
        _reject_user_url(request)
        api_key = _require_api_key()
        return _complete_once(self._client, api_key, request)


def _reject_user_url(request: object) -> None:
    if getattr(request, "url", None) is not None:
        _log_provider_error()
        raise ProviderError from None


def _require_api_key() -> str:
    raw_key = os.environ.get(DEEPSEEK_API_KEY_ENV)
    if raw_key is None or not raw_key.strip():
        structlog.get_logger(__name__).warning(
            "llm.generation_unavailable",
            error_code="generation_unavailable",
        )
        raise GenerationUnavailableError from None
    return raw_key


def _complete_once(
    client: httpx.Client,
    api_key: str,
    request: LlmRequest,
) -> LlmResult:
    try:
        response = client.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": TEMPERATURE,
                "stream": False,
            },
        )
    except httpx.HTTPError:
        _log_provider_error()
        raise ProviderError from None
    return _parse_response(response)


def _parse_response(response: httpx.Response) -> LlmResult:
    if response.status_code != HTTP_OK:
        _log_provider_error(status_code=response.status_code)
        raise ProviderError from None
    try:
        payload: object = response.json()
    except json.JSONDecodeError:
        _log_provider_error(status_code=response.status_code)
        raise ProviderError from None
    return _result_from_payload(payload)


def _result_from_payload(payload: object) -> LlmResult:
    try:
        result = _parse_payload(payload)
    except (AttributeError, IndexError, KeyError, TypeError):
        _log_provider_error()
        raise ProviderError from None
    if result.finish_reason != "stop":
        _log_provider_error()
        raise ProviderError from None
    return result


def _parse_payload(payload: object) -> LlmResult:
    if type(payload) is not dict:
        raise TypeError
    mapping = cast("dict[str, Any]", payload)
    choice = mapping["choices"][0]
    text = choice["message"]["content"]
    finish_reason = choice["finish_reason"]
    usage = mapping["usage"]
    prompt_tokens = usage["prompt_tokens"]
    completion_tokens = usage["completion_tokens"]
    total_tokens = usage["total_tokens"]
    if (
        type(text) is not str
        or type(finish_reason) is not str
        or type(prompt_tokens) is not int
        or type(completion_tokens) is not int
        or type(total_tokens) is not int
    ):
        raise TypeError
    return LlmResult(
        text=text,
        finish_reason=finish_reason,
        usage=LlmUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def _log_provider_error(*, status_code: int | None = None) -> None:
    if status_code is None:
        structlog.get_logger(__name__).warning(
            "llm.provider_error",
            error_code="provider_error",
        )
        return
    structlog.get_logger(__name__).warning(
        "llm.provider_error",
        error_code="provider_error",
        status_code=status_code,
    )
