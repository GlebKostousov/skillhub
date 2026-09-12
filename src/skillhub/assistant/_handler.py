"""Передаёт тело выбранного скилла и материал в шлюз модели."""

from abc import ABC, abstractmethod

from skillhub.assistant._prompt import build_generate_request
from skillhub.llm import LlmGateway
from skillhub.registry import Skill


class SkillHandler(ABC):
    """Доверенный обработчик одного выбранного скилла."""

    @abstractmethod
    def run(self, skill: Skill, material: str) -> str:
        """Выполняет выбранный скилл над материалом.

        Args:
            skill: выбранный проверенный скилл.
            material: недоверенные данные пользователя.

        Returns:
            Текстовый ответ модели.
        """


class PromptSkillHandler(SkillHandler):
    """Готовит текстовый ответ простого режима через шлюз."""

    def __init__(self, gateway: LlmGateway) -> None:
        """Сохраняет шлюз без обращения к файлам.

        Args:
            gateway: типизированная граница вызова модели.
        """
        self._gateway = gateway

    def run(self, skill: Skill, material: str) -> str:
        """Передаёт тело скилла как инструкцию и материал отдельно.

        Args:
            skill: выбранный скилл с доверенным телом.
            material: недоверенные данные пользователя.

        Returns:
            Текст успешного ответа шлюза.

        Raises:
            GenerationUnavailableError: отказ без ключа поставщика.
            ProviderError: отказ поставщика или неполный ответ.
        """
        result = self._gateway.complete(build_generate_request(skill, material))
        return result.text
