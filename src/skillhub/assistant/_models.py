"""Определяет неизменяемый исход обращения к ассистенту."""

from dataclasses import dataclass
from typing import Literal

AssistantOutcomeKind = Literal[
    "classified",
    "success",
    "none",
    "handler_unavailable",
    "generation_unavailable",
    "provider_error",
]


@dataclass(frozen=True, slots=True)
class AssistantOutcome:
    """Исход одного обращения к ассистенту.

    Attributes:
        selected_skill: имя выбранного режима или отсутствие выбора.
        caption: подпись выбранного скилла или отсутствие подписи.
        outcome: стабильный машинный исход.
        text: текстовый ответ или отсутствие текста.
        message: безопасное сообщение для пользователя.
    """

    selected_skill: str | None
    caption: str | None
    outcome: AssistantOutcomeKind
    text: str | None
    message: str
