"""Собирает нормативный протокол через порт генерации и шлюз модели."""

from skillhub.assistant._handler import SkillHandler
from skillhub.assistant._prompt import build_instruction_request
from skillhub.llm import LlmGateway
from skillhub.protocol import GeneratedDraft, create_draft, render
from skillhub.registry import Skill


class ProtocolSkillHandler(SkillHandler):
    """Собирает протокол через create_draft и возвращает нормативный Markdown."""

    def __init__(self, gateway: LlmGateway) -> None:
        """Сохраняет шлюз без обращения к файлам.

        Args:
            gateway: типизированная граница вызова модели.
        """
        self._generator = _GatewayProtocolGenerator(gateway)

    def run(self, skill: Skill, material: str) -> str:
        """Возвращает разобранный и воспроизведённый протокол.

        Args:
            skill: выбранный скилл протокола с доверенным телом.
            material: недоверенная транскрипция встречи.

        Returns:
            Нормативный Markdown принятого протокола.
        """
        protocol = create_draft(self._generator, skill.body, material)
        return render(protocol)


class _GatewayProtocolGenerator:
    """Адаптирует шлюз модели к узкому порту генерации протокола."""

    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    def generate(self, instruction: str, material: str) -> GeneratedDraft:
        """Формирует черновик по отдельным инструкции и материалу.

        Args:
            instruction: доверенная инструкция скилла.
            material: недоверенная транскрипция встречи.

        Returns:
            Текстовый ответ шлюза с причиной остановки.
        """
        result = self._gateway.complete(
            build_instruction_request(instruction, material)
        )
        return GeneratedDraft(text=result.text, finish_reason=result.finish_reason)
