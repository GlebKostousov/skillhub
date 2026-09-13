# ruff: noqa: RUF001
"""Собирает нормативный протокол через порт генерации и шлюз модели."""

from collections.abc import Mapping, Sequence

from skillhub.assistant._handler import SkillHandler
from skillhub.assistant._prompt import build_instruction_request
from skillhub.llm import LlmGateway
from skillhub.protocol import (
    FinalizedProtocol,
    GeneratedDraft,
    Protocol,
    create_draft,
    render,
    revise,
)
from skillhub.registry import Skill

_REVISE_ADDENDUM = """# Переписывание черновика

В недоверенном материале три блока: запись встречи,
черновик протокола и ответы человека на уточнения.
Собери итоговый протокол той же грамматики.
Ответы человека важнее черновика: поставь их в нужные поля
и согласуй формулировки вокруг.
Не выдумывай факты сверх записи встречи и ответов.
Пропущенные уточнения оставь как в черновике.
Верни только протокол."""


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

    def revise(
        self,
        skill: Skill,
        protocol: Protocol,
        answers: Sequence[Mapping[str, object]],
        material: str,
    ) -> FinalizedProtocol:
        """Переписывает черновик протокола вместе с ответами на уточнения.

        Args:
            skill: выбранный скилл протокола с доверенным телом.
            protocol: первый принятый черновик.
            answers: решения с идентификатором и действием.
            material: недоверенная транскрипция встречи.

        Returns:
            Протокол из повторного ответа порта.
        """
        return revise(
            protocol,
            answers,
            material,
            self._generator,
            f"{skill.body}\n\n{_REVISE_ADDENDUM}",
        )


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
