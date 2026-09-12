"""Собирает итоговый протокол из закрытых уточнений и проверки материала."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from skillhub.protocol._clarifications import apply_answers
from skillhub.protocol._models import Protocol
from skillhub.protocol._parser import parse
from skillhub.protocol._renderer import render
from skillhub.protocol._verify import UnconfirmedClaim, verify


@dataclass(frozen=True, slots=True)
class FinalizedProtocol:
    """Описывает итоговый протокол и неподтверждённые утверждения.

    Attributes:
        protocol: нормативный протокол после ответов и кругового разбора.
        unconfirmed: утверждения, которые материал не подтвердил.
    """

    protocol: Protocol
    unconfirmed: tuple[UnconfirmedClaim, ...]


def finalize(
    protocol: Protocol,
    answers: Sequence[Mapping[str, object]],
    material: str,
) -> FinalizedProtocol:
    """Собирает проверенный протокол из закрытых уточнений.

    Args:
        protocol: исходный протокол.
        answers: решения с идентификатором и действием.
        material: недоверенная транскрипция встречи.

    Returns:
        Итоговый протокол и список неподтверждённых утверждений.

    Raises:
        EmptyClarificationAnswerError: ответ пустой или пробельный.
        ExtraClarificationFieldError: решение содержит лишнее поле.
        InvalidClarificationAnswerError: ответ нельзя записать в поле задачи.
        UnknownClarificationIdError: идентификатор не входит в список.
        UnresolvedClarificationError: осталось незакрытое уточнение.
        VerifyMaterialTooLargeError: длина материала выше явного предела.
        ProtocolParseError: круговой разбор не принял собранный текст.
    """
    applied = apply_answers(protocol, answers, require_resolved=True)
    unconfirmed = verify(applied, material)
    return FinalizedProtocol(protocol=parse(render(applied)), unconfirmed=unconfirmed)
