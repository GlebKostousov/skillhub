"""Выбирает режим по намерению и снимку метаданных."""

from collections.abc import Sequence

import structlog

from skillhub.classifier._models import Classification, SkillMetadata
from skillhub.classifier._parse import parse_skill_name
from skillhub.classifier._prompt import build_request
from skillhub.llm import LlmGateway


def allowlist(metadata: Sequence[SkillMetadata]) -> frozenset[str]:
    """Строит allowlist имён из переданного снимка.

    Args:
        metadata: снимок метаданных валидных скиллов.

    Returns:
        Множество имён именно этого снимка.
    """
    return frozenset(item.name for item in metadata)


class SkillClassifier:
    """Выбирает один allowlist-режим или none через шлюз модели."""

    def __init__(self, gateway: LlmGateway) -> None:
        """Сохраняет шлюз без обращения к файлам и реестру.

        Args:
            gateway: типизированная граница вызова модели.
        """
        self._gateway = gateway

    def select(self, intent: str, metadata: Sequence[SkillMetadata]) -> Classification:
        """Выбирает режим по намерению и снимку метаданных.

        Args:
            intent: короткая задача пользователя.
            metadata: имена и описания валидных скиллов без тела.

        Returns:
            Выбранное allowlist-имя или отсутствие выбора.

        Raises:
            SkillHubError: типизированный отказ шлюза модели.
        """
        if _is_blank(intent):
            return _none()
        result = self._gateway.complete(build_request(intent, metadata))
        return _resolve(result.text, allowlist(metadata))


def _is_blank(intent: str) -> bool:
    return not intent.strip()


def _resolve(text: str, names: frozenset[str]) -> Classification:
    candidate = parse_skill_name(text)
    if candidate is None or candidate not in names:
        return _none()
    return _selected(candidate)


def _none() -> Classification:
    structlog.get_logger(__name__).info("classifier.none", error_code="none")
    return Classification(skill=None)


def _selected(name: str) -> Classification:
    structlog.get_logger(__name__).info(
        "classifier.selected",
        error_code="selected",
        skill_path=name,
    )
    return Classification(skill=name)
