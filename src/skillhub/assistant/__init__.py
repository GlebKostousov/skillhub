"""Предоставляет публичный фасад оркестрации простых режимов."""

from skillhub.assistant._assistant import Assistant
from skillhub.assistant._handler import PromptSkillHandler, SkillHandler
from skillhub.assistant._models import AssistantOutcome
from skillhub.assistant._registry import SkillHandlerRegistry

__all__ = [
    "Assistant",
    "AssistantOutcome",
    "PromptSkillHandler",
    "SkillHandler",
    "SkillHandlerRegistry",
]
