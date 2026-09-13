# ruff: noqa: RUF001
"""Собирает выбор режима и диспетчеризацию простого обработчика."""

from collections.abc import Mapping

import structlog

from skillhub.assistant._handler import SkillHandler
from skillhub.assistant._models import AssistantOutcome
from skillhub.assistant._registry import SkillHandlerRegistry
from skillhub.classifier import Classification, SkillClassifier, SkillMetadata
from skillhub.llm import GenerationUnavailableError, ProviderError
from skillhub.protocol import ProtocolGenerationError, ProtocolParseError
from skillhub.registry import Skill
from skillhub.usage import bind_call

_MESSAGE_SUCCESS = "Ответ готов."
_MESSAGE_CLASSIFIED = "Режим выбран."
_MESSAGE_NONE = (
    "Не поняла задачу. Напишите коротко, что сделать — "
    "например, собрать протокол встречи."
)
_MESSAGE_HANDLER_UNAVAILABLE = "Обработчик выбранного режима недоступен."


class Assistant:
    """Выбирает режим по снимку и запускает разрешённый обработчик."""

    def __init__(
        self,
        classifier: SkillClassifier,
        handlers: SkillHandlerRegistry,
    ) -> None:
        """Сохраняет классификатор и реестр обработчиков.

        Args:
            classifier: выбор режима по намерению и метаданным.
            handlers: разрешённые доверенные обработчики.
        """
        self._classifier = classifier
        self._handlers = handlers

    def classify(
        self,
        intent: str,
        snapshot: Mapping[str, Skill],
    ) -> AssistantOutcome:
        """Выбирает режим и сразу возвращает его подпись.

        Args:
            intent: короткая задача пользователя.
            snapshot: снимок валидных скиллов без публикации тела.

        Returns:
            Исход выбора режима без запуска обработчика.
        """
        with bind_call(operation="classify"):
            return _classify_only(self._classifier, intent, snapshot)

    def run(
        self,
        intent: str,
        material: str,
        snapshot: Mapping[str, Skill],
    ) -> AssistantOutcome:
        """Выбирает режим и готовит текстовый исход.

        Args:
            intent: короткая задача пользователя.
            material: недоверенные данные для выбранного режима.
            snapshot: снимок валидных скиллов без публикации тела в классификатор.

        Returns:
            Типизированный исход без маскировки отказов шлюза.
        """
        with bind_call(operation="classify"):
            return _select_and_run(
                self._classifier,
                self._handlers,
                intent,
                material,
                snapshot,
            )


def _select_and_run(
    classifier: SkillClassifier,
    handlers: SkillHandlerRegistry,
    intent: str,
    material: str,
    snapshot: Mapping[str, Skill],
) -> AssistantOutcome:
    try:
        classification = classifier.select(intent, _metadata_from(snapshot))
    except GenerationUnavailableError:
        return _gateway_unavailable(None, None)
    except ProviderError:
        return _gateway_provider(None, None)
    return _after_classification(handlers, classification, snapshot, material)


def _classify_only(
    classifier: SkillClassifier,
    intent: str,
    snapshot: Mapping[str, Skill],
) -> AssistantOutcome:
    try:
        classification = classifier.select(intent, _metadata_from(snapshot))
    except GenerationUnavailableError:
        return _gateway_unavailable(None, None)
    except ProviderError:
        return _gateway_provider(None, None)
    selected = classification.skill
    if selected is None:
        return _logged(
            AssistantOutcome(
                selected_skill=None,
                caption=None,
                outcome="none",
                text=None,
                message=_MESSAGE_NONE,
            )
        )
    skill = snapshot.get(selected)
    return _logged(
        AssistantOutcome(
            selected_skill=selected,
            caption=_caption_of(skill),
            outcome="classified",
            text=None,
            message=_MESSAGE_CLASSIFIED,
        )
    )


def _after_classification(
    handlers: SkillHandlerRegistry,
    classification: Classification,
    snapshot: Mapping[str, Skill],
    material: str,
) -> AssistantOutcome:
    selected = classification.skill
    if selected is None:
        return _logged(
            AssistantOutcome(
                selected_skill=None,
                caption=None,
                outcome="none",
                text=None,
                message=_MESSAGE_NONE,
            )
        )
    return _run_selected(handlers, selected, snapshot, material)


def _run_selected(
    handlers: SkillHandlerRegistry,
    selected: str,
    snapshot: Mapping[str, Skill],
    material: str,
) -> AssistantOutcome:
    skill = snapshot.get(selected)
    handler = handlers.resolve(selected)
    if handler is None or skill is None:
        return _unavailable(selected, _caption_of(skill))
    with bind_call(operation="generate", skill=selected):
        return _run_handler(handler, skill, material)


def _unavailable(selected: str, caption: str | None) -> AssistantOutcome:
    return _logged(
        AssistantOutcome(
            selected_skill=selected,
            caption=caption,
            outcome="handler_unavailable",
            text=None,
            message=_MESSAGE_HANDLER_UNAVAILABLE,
        )
    )


def _caption_of(skill: Skill | None) -> str | None:
    if skill is None:
        return None
    return skill.caption


def _metadata_from(snapshot: Mapping[str, Skill]) -> tuple[SkillMetadata, ...]:
    return tuple(
        SkillMetadata(name=skill.name, description=skill.description)
        for skill in snapshot.values()
    )


def _run_handler(
    handler: SkillHandler,
    skill: Skill,
    material: str,
) -> AssistantOutcome:
    try:
        text = handler.run(skill, material)
    except GenerationUnavailableError:
        return _gateway_unavailable(skill.name, skill.caption)
    except ProviderError:
        return _gateway_provider(skill.name, skill.caption)
    except (ProtocolGenerationError, ProtocolParseError) as error:
        return _logged(
            AssistantOutcome(
                selected_skill=skill.name,
                caption=skill.caption,
                outcome="provider_error",
                text=None,
                message=error.public_message,
            )
        )
    return _logged(
        AssistantOutcome(
            selected_skill=skill.name,
            caption=skill.caption,
            outcome="success",
            text=text,
            message=_MESSAGE_SUCCESS,
        )
    )


def _gateway_unavailable(
    selected: str | None,
    caption: str | None,
) -> AssistantOutcome:
    return _logged(
        AssistantOutcome(
            selected_skill=selected,
            caption=caption,
            outcome="generation_unavailable",
            text=None,
            message=GenerationUnavailableError.public_message,
        )
    )


def _gateway_provider(
    selected: str | None,
    caption: str | None,
) -> AssistantOutcome:
    return _logged(
        AssistantOutcome(
            selected_skill=selected,
            caption=caption,
            outcome="provider_error",
            text=None,
            message=ProviderError.public_message,
        )
    )


def _logged(outcome: AssistantOutcome) -> AssistantOutcome:
    logger = structlog.get_logger(__name__)
    if outcome.selected_skill is None:
        logger.info("assistant.outcome", error_code=outcome.outcome)
    else:
        logger.info(
            "assistant.outcome",
            error_code=outcome.outcome,
            skill_path=outcome.selected_skill,
        )
    return outcome
