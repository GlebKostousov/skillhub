"""Проверяет оркестрацию выбора режима и диспетчеризации."""

from pathlib import Path

import pytest

from skillhub.assistant import (
    Assistant,
    AssistantOutcome,
    PromptSkillHandler,
    SkillHandlerRegistry,
)
from skillhub.classifier import SkillClassifier
from skillhub.core import SkillHubError
from skillhub.llm import (
    FakeLlmGateway,
    GenerationUnavailableError,
    LlmRequest,
    LlmResult,
    LlmUsage,
    ProviderError,
)
from skillhub.registry import Skill, SkillRegistry

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_ROOT = _REPOSITORY_ROOT / "skills"
_HTML_RESULT = "<script>alert(1)</script><p>абзац</p>"
_MATERIAL = "MATERIAL-orchestration-payload"

_SIMPLE_NAMES = (
    "business-message",
    "meeting-action-items",
    "text-summary",
    "text-translation",
)


def _result(text: str) -> LlmResult:
    """Собирает успешный ответ тестового шлюза."""
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _skill(
    name: str,
    *,
    caption: str | None = None,
    body: str | None = None,
) -> Skill:
    """Собирает скилл снимка для оркестрации."""
    return Skill(
        name=name,
        caption=caption or f"Подпись {name}",
        description=f"Описание {name}",
        body=body or f"TRUSTED-BODY-{name}",
        has_files=name == "meeting-protocol",
    )


def _snapshot(*names: str) -> dict[str, Skill]:
    """Собирает снимок перечисленных режимов."""
    return {name: _skill(name) for name in names}


def _full_snapshot() -> dict[str, Skill]:
    """Собирает снимок пяти режимов, включая протокол."""
    return _snapshot("meeting-protocol", *_SIMPLE_NAMES)


def _assistant(
    classify_text: str,
    generate_text: str = "готовый текст",
) -> tuple[Assistant, FakeLlmGateway, FakeLlmGateway]:
    """Собирает ассистента с раздельными шлюзами выбора и генерации."""
    classify_gateway = FakeLlmGateway(result=_result(classify_text))
    generate_gateway = FakeLlmGateway(result=_result(generate_text))
    assistant = Assistant(
        SkillClassifier(classify_gateway),
        SkillHandlerRegistry(PromptSkillHandler(generate_gateway)),
    )
    return assistant, classify_gateway, generate_gateway


def _sent_text(request: LlmRequest) -> str:
    """Склеивает содержимое отправленного запроса."""
    return "\n".join(message.content for message in request.messages)


def _system_text(request: LlmRequest) -> str:
    """Возвращает системную инструкцию запроса."""
    system = [
        message.content for message in request.messages if message.role == "system"
    ]
    assert len(system) == 1
    return system[0]


@pytest.mark.parametrize("name", _SIMPLE_NAMES)
def test_each_simple_mode_uses_own_body_and_skill_caption(name: str) -> None:
    """Проверяет собственное тело и подпись из скилла, а не из модели."""
    assistant, _classify, generate = _assistant(
        f'{{"skill": "{name}"}}',
        generate_text=f"caption: подделанная подпись {name}",
    )
    snapshot = _full_snapshot()

    outcome = assistant.run("Сделай по режиму", _MATERIAL, snapshot)

    assert outcome == AssistantOutcome(
        selected_skill=name,
        caption=snapshot[name].caption,
        outcome="success",
        text=f"caption: подделанная подпись {name}",
        message="Ответ готов.",
    )
    assert len(generate.requests) == 1
    assert _system_text(generate.requests[0]) == snapshot[name].body
    for other in _SIMPLE_NAMES:
        if other != name:
            assert snapshot[other].body not in _sent_text(generate.requests[0])


def test_real_simple_skills_receive_distinct_bodies() -> None:
    """Проверяет, что реальные четыре SKILL.md дают разные тела."""
    report = SkillRegistry(_SKILLS_ROOT).load()
    snapshot = {skill.name: skill for skill in report.skills}
    bodies = {name: snapshot[name].body for name in _SIMPLE_NAMES}
    assert len(set(bodies.values())) == 4

    for name in _SIMPLE_NAMES:
        assistant, _classify, generate = _assistant(f'{{"skill": "{name}"}}')
        outcome = assistant.run("Сделай по режиму", _MATERIAL, snapshot)
        assert outcome.outcome == "success"
        assert outcome.selected_skill == name
        assert outcome.caption == snapshot[name].caption
        sent = _sent_text(generate.requests[0])
        assert snapshot[name].body in sent
        for other in _SIMPLE_NAMES:
            if other != name:
                assert snapshot[other].body not in sent


def test_none_does_not_generate() -> None:
    """Проверяет исход none без вызова генерации."""
    assistant, classify, generate = _assistant('{"skill": null}')

    outcome = assistant.run("Какая завтра погода?", _MATERIAL, _full_snapshot())

    assert outcome == AssistantOutcome(
        selected_skill=None,
        caption=None,
        outcome="none",
        text=None,
        message="Подходящий режим не выбран.",
    )
    assert len(classify.requests) == 1
    assert generate.requests == []


def test_meeting_protocol_is_handler_unavailable_without_generate() -> None:
    """Проверяет handler_unavailable без генерации и без Word."""
    assistant, classify, generate = _assistant('{"skill": "meeting-protocol"}')
    snapshot = _full_snapshot()

    outcome = assistant.run("Составь протокол совещания", _MATERIAL, snapshot)

    assert outcome == AssistantOutcome(
        selected_skill="meeting-protocol",
        caption=snapshot["meeting-protocol"].caption,
        outcome="handler_unavailable",
        text=None,
        message="Обработчик выбранного режима недоступен.",
    )
    assert generate.requests == []
    assert _MATERIAL not in "".join(
        _sent_text(request) for request in classify.requests
    )


def test_html_outcome_text_is_plain() -> None:
    """Проверяет, что HTML ответа остаётся текстом исхода."""
    assistant, _classify, _generate = _assistant(
        '{"skill": "text-summary"}',
        generate_text=_HTML_RESULT,
    )

    outcome = assistant.run("Суммируй", "<b>сырьё</b>", _full_snapshot())

    assert outcome.outcome == "success"
    assert outcome.text == _HTML_RESULT


def test_classify_generation_unavailable_is_outcome() -> None:
    """Проверяет код generation_unavailable на этапе выбора."""
    classify = FakeLlmGateway(error=GenerationUnavailableError())
    generate = FakeLlmGateway(result=_result("не должен вызываться"))
    assistant = Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(PromptSkillHandler(generate)),
    )

    outcome = assistant.run("Суммируй", _MATERIAL, _full_snapshot())

    assert outcome.outcome == "generation_unavailable"
    assert outcome.text is None
    assert outcome.selected_skill is None
    assert outcome.caption is None
    assert outcome.message == GenerationUnavailableError.public_message
    assert isinstance(GenerationUnavailableError(), SkillHubError)
    assert generate.requests == []


def test_classify_provider_error_is_outcome() -> None:
    """Проверяет код provider_error на этапе выбора."""
    classify = FakeLlmGateway(error=ProviderError())
    generate = FakeLlmGateway(result=_result("не должен вызываться"))
    assistant = Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(PromptSkillHandler(generate)),
    )

    outcome = assistant.run("Суммируй", _MATERIAL, _full_snapshot())

    assert outcome.outcome == "provider_error"
    assert outcome.message == ProviderError.public_message
    assert generate.requests == []


def test_generate_generation_unavailable_keeps_selected_skill() -> None:
    """Проверяет отказ генерации без маскировки успехом."""
    classify = FakeLlmGateway(result=_result('{"skill": "text-summary"}'))
    generate = FakeLlmGateway(error=GenerationUnavailableError())
    assistant = Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(PromptSkillHandler(generate)),
    )
    snapshot = _full_snapshot()

    outcome = assistant.run("Суммируй", _MATERIAL, snapshot)

    assert outcome == AssistantOutcome(
        selected_skill="text-summary",
        caption=snapshot["text-summary"].caption,
        outcome="generation_unavailable",
        text=None,
        message=GenerationUnavailableError.public_message,
    )


def test_generate_provider_error_keeps_selected_skill() -> None:
    """Проверяет отказ поставщика на генерации без маскировки успехом."""
    classify = FakeLlmGateway(result=_result('{"skill": "text-translation"}'))
    generate = FakeLlmGateway(error=ProviderError())
    assistant = Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(PromptSkillHandler(generate)),
    )
    snapshot = _full_snapshot()

    outcome = assistant.run("Переведи", _MATERIAL, snapshot)

    assert outcome.outcome == "provider_error"
    assert outcome.selected_skill == "text-translation"
    assert outcome.caption == snapshot["text-translation"].caption
    assert outcome.text is None
    assert outcome.message == ProviderError.public_message


def test_replay_same_skill_keeps_its_body() -> None:
    """Проверяет, что повтор выбранного скилла не подменяет тело."""
    assistant, _classify, generate = _assistant('{"skill": "text-summary"}')
    snapshot = _full_snapshot()

    first = assistant.run("Суммируй", "первый", snapshot)
    second = assistant.run("Суммируй", "второй", snapshot)

    assert first.outcome == second.outcome == "success"
    assert [_system_text(request) for request in generate.requests] == [
        snapshot["text-summary"].body,
        snapshot["text-summary"].body,
    ]
    assert snapshot["text-translation"].body not in _sent_text(generate.requests[0])
    assert snapshot["text-translation"].body not in _sent_text(generate.requests[1])
