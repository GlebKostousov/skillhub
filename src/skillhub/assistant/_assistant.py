"""Собирает выбор режима и диспетчеризацию простого обработчика."""

from collections.abc import Mapping

import structlog

from skillhub.assistant._handler import SkillHandler
from skillhub.assistant._models import AssistantOutcome
from skillhub.assistant._registry import SkillHandlerRegistry
from skillhub.classifier import SkillClassifier, SkillMetadata
from skillhub.llm import GenerationUnavailableError, ProviderError
from skillhub.protocol import ProtocolGenerationError, ProtocolParseError
from skillhub.registry import Skill

_MESSAGE_SUCCESS = "Ответ готов."
_MESSAGE_NONE = "Подходящий режим не выбран."
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
        try:
            classification = self._classifier.select(intent, _metadata_from(snapshot))
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
        caption = skill.caption if skill is not None else None
        handler = self._handlers.resolve(selected)
        if handler is None or skill is None:
            return _logged(
                AssistantOutcome(
                    selected_skill=selected,
                    caption=caption,
                    outcome="handler_unavailable",
                    text=None,
                    message=_MESSAGE_HANDLER_UNAVAILABLE,
                )
            )
        return _run_handler(handler, skill, material)


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
