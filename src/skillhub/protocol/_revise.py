# ruff: noqa: RUF001
"""Собирает повторную генерацию протокола из черновика и ответов."""

from collections.abc import Mapping, Sequence

from skillhub.protocol._clarifications import apply_answers, build_clarifications
from skillhub.protocol._draft import ProtocolTextGenerator, create_draft
from skillhub.protocol._finalize import FinalizedProtocol
from skillhub.protocol._models import Protocol
from skillhub.protocol._renderer import render
from skillhub.protocol._verify import verify

_MATERIAL_HEADING = "ЗАПИСЬ ВСТРЕЧИ"
_DRAFT_HEADING = "ЧЕРНОВИК ПРОТОКОЛА"
_ANSWERS_HEADING = "ОТВЕТЫ НА УТОЧНЕНИЯ"
_SKIPPED = "пропущено"


def revise(
    protocol: Protocol,
    answers: Sequence[Mapping[str, object]],
    material: str,
    generator: ProtocolTextGenerator,
    instruction: str,
) -> FinalizedProtocol:
    """Собирает переписанный протокол из первого черновика и ответов.

    Args:
        protocol: исходный черновик протокола.
        answers: решения с идентификатором и действием.
        material: недоверенная транскрипция встречи.
        generator: порт повторной генерации.
        instruction: доверенная инструкция скилла и дополнение.

    Returns:
        Протокол из ответа порта и список неподтверждённых утверждений.

    Raises:
        EmptyClarificationAnswerError: ответ пустой или пробельный.
        ExtraClarificationFieldError: решение содержит лишнее поле.
        InvalidClarificationAnswerError: ответ нельзя записать в поле задачи.
        UnknownClarificationIdError: идентификатор не входит в список.
        UnresolvedClarificationError: осталось незакрытое уточнение.
        VerifyMaterialTooLargeError: длина материала выше явного предела.
        ProtocolGenerationError: причина остановки порта не stop.
        ProtocolParseError: текст ответа не соответствует грамматике.
    """
    apply_answers(protocol, answers, require_resolved=True)
    rewritten = create_draft(
        generator,
        instruction,
        _revise_material(protocol, answers, material),
    )
    answered = {
        str(item["id"])
        for item in answers
        if isinstance(item, Mapping) and item.get("action") == "answer"
    }
    unconfirmed = tuple(
        claim for claim in verify(rewritten, material) if claim.target not in answered
    )
    return FinalizedProtocol(protocol=rewritten, unconfirmed=unconfirmed)


def _revise_material(
    protocol: Protocol,
    answers: Sequence[Mapping[str, object]],
    material: str,
) -> str:
    notes = "\n".join(_answer_notes(protocol, answers))
    return (
        f"{_MATERIAL_HEADING}\n\n{material}\n\n"
        f"{_DRAFT_HEADING}\n\n{render(protocol)}\n\n"
        f"{_ANSWERS_HEADING}\n\n{notes}"
    )


def _answer_notes(
    protocol: Protocol,
    answers: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    labels = {item.id: item.reason for item in build_clarifications(protocol)}
    notes: list[str] = []
    for item in answers:
        identifier = str(item.get("id", ""))
        label = labels.get(identifier, identifier)
        if item.get("action") == "skip":
            notes.append(f"{label} — {_SKIPPED}")
            continue
        notes.append(f"{label} — {item.get('value', '')}")
    return tuple(notes)
