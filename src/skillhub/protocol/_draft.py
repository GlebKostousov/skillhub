"""Собирает нормативный протокол из ответа порта генерации."""

import typing
from dataclasses import dataclass

from skillhub.protocol._errors import ProtocolGenerationError
from skillhub.protocol._models import Protocol
from skillhub.protocol._parser import parse


@dataclass(frozen=True, slots=True)
class GeneratedDraft:
    """Описывает текстовый ответ порта до разбора протокола.

    Attributes:
        text: текст ответа, ещё не принятый как протокол.
        finish_reason: причина остановки поставщика.
    """

    text: str
    finish_reason: str


class ProtocolTextGenerator(typing.Protocol):
    """Описывает узкий порт генерации без шлюза поставщика.

    Вызов держит доверенную инструкцию и недоверенный материал в разных
    аргументах, не склеивая их в одну роль. Контракт вызова: temperature 0,
    пределы размера входа и выходных токенов задаёт реализация порта.
    """

    def generate(self, instruction: str, material: str) -> GeneratedDraft:
        """Формирует черновик по отдельным инструкции и материалу.

        Args:
            instruction: доверенная инструкция скилла.
            material: недоверенная транскрипция встречи.

        Returns:
            Текстовый ответ порта с причиной остановки.
        """
        ...


def create_draft(
    generator: ProtocolTextGenerator,
    instruction: str,
    material: str,
) -> Protocol:
    """Возвращает протокол из ответа порта генерации.

    Args:
        generator: порт текстовой генерации.
        instruction: доверенная инструкция скилла.
        material: недоверенная транскрипция встречи.

    Returns:
        Разобранный протокол без сырого текста ответа.

    Raises:
        ProtocolGenerationError: причина остановки порта не stop.
        ProtocolParseError: текст ответа не соответствует грамматике.
    """
    draft = generator.generate(instruction, material)
    _require_completed_draft(draft)
    return parse(draft.text)


def _require_completed_draft(draft: GeneratedDraft) -> None:
    """Отклоняет ответ порта, если генерация не завершилась штатно.

    Args:
        draft: черновик порта с причиной остановки.
    """
    if draft.finish_reason != "stop":
        raise ProtocolGenerationError
