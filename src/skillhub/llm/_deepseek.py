"""Вызывает DeepSeek через httpx без пользовательского URL."""

import json
import os
from typing import Any, cast

import httpx
import structlog

from skillhub.llm._constants import DEEPSEEK_API_KEY_ENV, DEEPSEEK_API_URL, HTTP_OK
from skillhub.llm._errors import GenerationUnavailableError, ProviderError
from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult, LlmUsage
from skillhub.runtime import OverlayValues, RuntimeStore
from skillhub.runtime._schema import seed_values

_UNUSED_REASONING_EFFORT = "high"
_UNUSED_TOP_P = 1.0
_UNUSED_PENALTY = 0
_UNUSED_RESPONSE_FORMAT = "text"


class DeepSeekLlmGateway(LlmGateway):
    """Отправляет один запрос к фиксированному адресу DeepSeek."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        store: RuntimeStore | None = None,
    ) -> None:
        """Сохраняет HTTP-клиент и источник снимка без чтения ключа.

        Args:
            client: готовый клиент или штатный клиент пакета.
            store: хранилище runtime-настроек или посев констант.
        """
        self._client = client or httpx.Client(follow_redirects=False)
        self._store = store

    def complete(self, request: LlmRequest) -> LlmResult:
        """Выполняет один вызов поставщика по замороженному снимку.

        Args:
            request: типизированный запрос без пользовательского URL.

        Returns:
            Успешный результат с текстом, причиной и usage.
        """
        return self.complete_with_values(request, _snapshot_values(self._store))

    def complete_with_values(
        self,
        request: LlmRequest,
        values: OverlayValues,
    ) -> LlmResult:
        """Выполняет вызов по уже замороженным значениям снимка.

        Args:
            request: типизированный запрос без пользовательского URL.
            values: снимок, общий с резервом и записью расхода.

        Returns:
            Успешный результат с текстом, причиной и usage.
        """
        _reject_user_url(request)
        api_key = _resolve_api_key()
        return _complete_once(self._client, api_key, request, values)


def _snapshot_values(store: RuntimeStore | None) -> OverlayValues:
    if store is None:
        return seed_values(None)
    return store.snapshot().values


def _reject_user_url(request: object) -> None:
    if getattr(request, "url", None) is not None:
        _log_provider_error()
        raise ProviderError from None


def _resolve_api_key() -> str:
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
    values: OverlayValues,
) -> LlmResult:
    try:
        response = client.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=_provider_body(request, values),
            timeout=values.timeout,
        )
    except httpx.HTTPError:
        _log_provider_error()
        raise ProviderError from None
    return _parse_response(response)


def _provider_body(request: LlmRequest, values: OverlayValues) -> dict[str, object]:
    body: dict[str, object] = {
        "model": values.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "max_tokens": values.max_tokens,
        "temperature": values.temperature,
        "stream": False,
    }
    if values.thinking == "disabled":
        body["thinking"] = {"type": "disabled"}
    if values.reasoning_effort != _UNUSED_REASONING_EFFORT:
        body["reasoning_effort"] = values.reasoning_effort
    if values.top_p != _UNUSED_TOP_P:
        body["top_p"] = values.top_p
    if values.frequency_penalty != _UNUSED_PENALTY:
        body["frequency_penalty"] = values.frequency_penalty
    if values.presence_penalty != _UNUSED_PENALTY:
        body["presence_penalty"] = values.presence_penalty
    if values.stop:
        body["stop"] = list(values.stop)
    if values.response_format != _UNUSED_RESPONSE_FORMAT:
        body["response_format"] = {"type": values.response_format}
    return body


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
