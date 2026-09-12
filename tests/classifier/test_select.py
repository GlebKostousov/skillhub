"""Проверяет публичный фасад выбора режима."""

import json

import pytest

from skillhub.classifier import (
    Classification,
    SkillClassifier,
    SkillMetadata,
    allowlist,
)
from skillhub.core import SkillHubError
from skillhub.llm import (
    FakeLlmGateway,
    GenerationUnavailableError,
    LlmResult,
    LlmUsage,
    ProviderError,
)

_FIXTURE_METADATA = (
    SkillMetadata(
        name="meeting-protocol",
        description="Полный формальный протокол встречи.",
    ),
    SkillMetadata(
        name="meeting-action-items",
        description="Извлечение только задач встречи.",
    ),
    SkillMetadata(
        name="text-summary",
        description="Краткий пересказ произвольного текста.",
    ),
    SkillMetadata(
        name="text-translation",
        description="Перевод материала на указанный язык.",
    ),
    SkillMetadata(
        name="business-message",
        description="Составление делового письма по фактам.",
    ),
)

_INTENT_BY_SKILL = {
    "meeting-protocol": "Составь протокол совещания",
    "meeting-action-items": "Выпиши задачи встречи",
    "text-summary": "Суммируй статью коротко",
    "text-translation": "Переведи текст на английский",
    "business-message": "Напиши деловое письмо клиенту",
}


def _result(text: str) -> LlmResult:
    """Собирает успешный ответ тестового шлюза."""
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _command(name: str | None) -> str:
    """Собирает структурированную команду модели."""
    return json.dumps({"skill": name}, ensure_ascii=True)


def _classifier(text: str) -> tuple[SkillClassifier, FakeLlmGateway]:
    """Собирает классификатор с детерминированным ответом."""
    gateway = FakeLlmGateway(result=_result(text))
    return SkillClassifier(gateway), gateway


@pytest.mark.parametrize("skill", list(_INTENT_BY_SKILL))
def test_select_returns_each_fixture_class(skill: str) -> None:
    """Проверяет выбор каждого класса из фикстур метаданных."""
    classifier, _gateway = _classifier(_command(skill))

    classification = classifier.select(_INTENT_BY_SKILL[skill], _FIXTURE_METADATA)

    assert classification == Classification(skill=skill)


def test_select_returns_none_for_irrelevant_intent() -> None:
    """Проверяет отказ none при явном null от модели."""
    classifier, _gateway = _classifier(_command(None))

    classification = classifier.select("Какая завтра погода?", _FIXTURE_METADATA)

    assert classification == Classification(skill=None)


def test_blank_intent_is_none_without_model_call() -> None:
    """Проверяет deny пустого намерения без вызова модели."""
    gateway = FakeLlmGateway(result=_result(_command("meeting-protocol")))
    classifier = SkillClassifier(gateway)

    classification = classifier.select("   \n\t  ", _FIXTURE_METADATA)

    assert classification == Classification(skill=None)
    assert gateway.requests == []


def test_empty_intent_is_none_without_model_call() -> None:
    """Проверяет deny пустой строки без вызова модели."""
    gateway = FakeLlmGateway(result=_result(_command("text-summary")))

    classification = SkillClassifier(gateway).select("", _FIXTURE_METADATA)

    assert classification.skill is None
    assert gateway.requests == []


def test_unknown_name_from_model_is_none() -> None:
    """Проверяет none для имени вне allowlist снимка."""
    classifier, _gateway = _classifier(_command("leaked-skill"))

    classification = classifier.select("Составь протокол совещания", _FIXTURE_METADATA)

    assert classification == Classification(skill=None)


def test_invalid_json_is_none() -> None:
    """Проверяет none при битом JSON ответа модели."""
    classifier, _gateway = _classifier("{not-json")

    classification = classifier.select("Составь протокол совещания", _FIXTURE_METADATA)

    assert classification == Classification(skill=None)


def test_allowlist_uses_snapshot_names_not_global_hardcode() -> None:
    """Проверяет, что allowlist берётся из переданного снимка."""
    metadata = (
        SkillMetadata(name="custom-review", description="Разбор кода по чеклисту."),
    )
    classifier, _gateway = _classifier(_command("custom-review"))

    classification = classifier.select("Проверь этот патч", metadata)

    assert classification == Classification(skill="custom-review")
    assert allowlist(metadata) == frozenset({"custom-review"})
    assert "meeting-protocol" not in allowlist(metadata)


def test_same_intent_and_snapshot_are_stable() -> None:
    """Проверяет стабильный класс при детерминированном шлюзе."""
    gateway = FakeLlmGateway(result=_result(_command("text-summary")))
    classifier = SkillClassifier(gateway)
    intent = _INTENT_BY_SKILL["text-summary"]

    first = classifier.select(intent, _FIXTURE_METADATA)
    second = classifier.select(intent, _FIXTURE_METADATA)

    assert first == second == Classification(skill="text-summary")


def test_gateway_generation_unavailable_is_not_masked() -> None:
    """Проверяет проброс типизированного отказа без ключа."""
    gateway = FakeLlmGateway(error=GenerationUnavailableError())

    with pytest.raises(GenerationUnavailableError) as captured:
        SkillClassifier(gateway).select("Составь протокол", _FIXTURE_METADATA)

    assert isinstance(captured.value, SkillHubError)
    assert captured.value.code == "generation_unavailable"


def test_gateway_provider_error_is_not_masked() -> None:
    """Проверяет проброс отказа поставщика без маскировки none."""
    gateway = FakeLlmGateway(error=ProviderError())

    with pytest.raises(ProviderError) as captured:
        SkillClassifier(gateway).select("Составь протокол", _FIXTURE_METADATA)

    assert captured.value.code == "provider_error"


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "{}",
        '{"skill": 1}',
        '{"name": "meeting-protocol"}',
    ],
)
def test_invalid_structured_command_is_none(text: str) -> None:
    """Проверяет none для структурно неверной команды модели."""
    classifier, _gateway = _classifier(text)

    classification = classifier.select("Составь протокол совещания", _FIXTURE_METADATA)

    assert classification == Classification(skill=None)
