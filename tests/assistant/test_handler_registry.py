"""Проверяет реестр обработчиков простых режимов."""

from skillhub.assistant import PromptSkillHandler, SkillHandlerRegistry
from skillhub.llm import FakeLlmGateway, LlmResult, LlmUsage
from skillhub.registry import Skill

_SIMPLE_NAMES = (
    "business-message",
    "meeting-action-items",
    "text-summary",
    "text-translation",
)


def _handler() -> PromptSkillHandler:
    """Собирает обработчик с успешным тестовым шлюзом."""
    return PromptSkillHandler(
        FakeLlmGateway(
            result=LlmResult(
                text="ок",
                finish_reason="stop",
                usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        )
    )


def test_four_simple_names_resolve_to_prompt_handler() -> None:
    """Проверяет, что четыре простых имени ведут в PromptSkillHandler."""
    handler = _handler()
    registry = SkillHandlerRegistry(handler)

    for name in _SIMPLE_NAMES:
        assert registry.resolve(name) is handler


def test_meeting_protocol_is_absent_and_not_success() -> None:
    """Проверяет, что meeting-protocol нет в реестре и это не успех."""
    registry = SkillHandlerRegistry(_handler())

    assert registry.resolve("meeting-protocol") is None
    skill = Skill(
        name="meeting-protocol",
        caption="Протокол встречи",
        description="Полный протокол.",
        body="TRUSTED-BODY-meeting-protocol",
        has_files=True,
    )
    resolved = registry.resolve(skill.name)
    assert resolved is None


def test_unknown_name_is_unavailable() -> None:
    """Проверяет отказ для имени вне четырёх простых режимов."""
    registry = SkillHandlerRegistry(_handler())

    assert registry.resolve("leaked-skill") is None
    assert registry.resolve("") is None
