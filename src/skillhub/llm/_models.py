"""Определяет неизменяемые контракты вызова модели."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """Одно сообщение в запросе к модели.

    Attributes:
        role: допустимая роль собеседника.
        content: текст сообщения без адреса поставщика.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """Типизированный запрос без пользовательского URL и имени модели.

    Attributes:
        messages: упорядоченные сообщения одного вызова.
    """

    messages: tuple[LlmMessage, ...]


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Фактическое использование токенов из ответа поставщика.

    Attributes:
        prompt_tokens: число входных токенов.
        completion_tokens: число выходных токенов.
        total_tokens: суммарное число токенов.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class LlmResult:
    """Успешный типизированный ответ шлюза.

    Attributes:
        text: текст ответа модели.
        finish_reason: причина завершения, принятая только как stop.
        usage: фактическое использование токенов.
    """

    text: str
    finish_reason: str
    usage: LlmUsage


class LlmGateway(ABC):
    """Типизированная граница вызова поставщика модели."""

    @abstractmethod
    def complete(self, request: LlmRequest) -> LlmResult:
        """Выполняет один вызов модели.

        Args:
            request: проверенный запрос без пользовательского URL.

        Returns:
            Типизированный успешный результат.
        """
