"""Предоставляет публичный фасад изолированного шлюза модели."""

from skillhub.llm._constants import (
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    MAX_OUTPUT_TOKENS,
    REQUEST_TIMEOUT_SECONDS,
)
from skillhub.llm._deepseek import DeepSeekLlmGateway
from skillhub.llm._errors import GenerationUnavailableError, ProviderError
from skillhub.llm._fake import FakeLlmGateway
from skillhub.llm._metered import MeteredLlmGateway
from skillhub.llm._models import LlmGateway, LlmMessage, LlmRequest, LlmResult, LlmUsage

__all__ = [
    "DEEPSEEK_API_URL",
    "DEEPSEEK_MODEL",
    "MAX_OUTPUT_TOKENS",
    "REQUEST_TIMEOUT_SECONDS",
    "DeepSeekLlmGateway",
    "FakeLlmGateway",
    "GenerationUnavailableError",
    "LlmGateway",
    "LlmMessage",
    "LlmRequest",
    "LlmResult",
    "LlmUsage",
    "MeteredLlmGateway",
    "ProviderError",
]
