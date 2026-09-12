# ruff: noqa: RUF001
"""Проверяет утверждения протокола явными маркерами в материале."""

import re
from dataclasses import dataclass
from typing import Literal

from skillhub.protocol._constants import MAX_PROTOCOL_CHARS, PLACEHOLDER
from skillhub.protocol._errors import VerifyMaterialTooLargeError
from skillhub.protocol._models import Protocol, ProtocolTask

MAX_VERIFY_CHARS = MAX_PROTOCOL_CHARS
"""Максимальная длина материала для детерминированной проверки."""

_SPEAKER = re.compile(
    r"(?m)^(?:\[[^\]]{1,40}\]|[0-9A-Za-zА-Яа-яЁё][\w. -]{0,40}):\s*"
)
_CLAUSE = re.compile(r"[.!?]+")
_CONFIRM = (
    "решили",
    "решено",
    "приняли",
    "принято",
    "поручено",
    "поручили",
    "утвердили",
    "утверждено",
    "назначили",
    "назначено",
)
_NEGATE = (
    "не решили",
    "не решено",
    "не приняли",
    "не принято",
    "не поручено",
    "не поручили",
    "не утвердили",
    "не утверждено",
    "не назначили",
    "не назначено",
    "отменили",
    "отменено",
)
_DISCUSS = (
    "обсуждали",
    "обсудили",
    "обсуждаем",
    "обсуждение",
    "пожелание",
    "хотели бы",
    "предлагаю",
    "давайте обсудим",
)
_ATTACK = (
    "игнорируй",
    "ignore",
    "забудь правила",
    "запиши решение",
    "занеси решение",
)
_REASON_DISCUSSION = (
    "Обсуждение не подтверждает решение, задачу, срок или ответственного."
)
_REASON_CONFLICT = "В материале есть противоречие по этому утверждению."
_REASON_ATTACK = "Атакующая формулировка не подтверждает утверждение."
_REASON_NO_MARKER = "Утверждение не подтверждено явным маркером в материале."

type _Hit = Literal["confirm", "negate", "discuss", "attack", "mention"]


@dataclass(frozen=True, slots=True)
class UnconfirmedClaim:
    """Описывает утверждение протокола, не подтверждённое материалом.

    Attributes:
        target: целевое поле утверждения.
        reason: причина, по которой утверждение не подтверждено.
    """

    target: str
    reason: str


def verify(protocol: Protocol, material: str) -> tuple[UnconfirmedClaim, ...]:
    """Возвращает неподтверждённые утверждения протокола по материалу.

    Args:
        protocol: нормативный протокол встречи.
        material: недоверенная транскрипция встречи.

    Returns:
        Отметки неподтверждённых решений, задач, сроков и ответственных.

    Raises:
        VerifyMaterialTooLargeError: длина материала выше явного предела.
    """
    if len(material) > MAX_VERIFY_CHARS:
        raise VerifyMaterialTooLargeError
    prepared = _prepare(material)
    return tuple(
        UnconfirmedClaim(target, reason)
        for target, text in _iter_claims(protocol)
        if (reason := _unconfirmed_reason(prepared, text)) is not None
    )


def _iter_claims(protocol: Protocol) -> tuple[tuple[str, str], ...]:
    claims = [
        (f"decision:{index}", text)
        for index, text in enumerate(protocol.decisions)
        if text != PLACEHOLDER
    ]
    for index, task in enumerate(protocol.tasks):
        claims.extend(_task_claims(index, task))
    return tuple(claims)


def _task_claims(index: int, task: ProtocolTask) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    if task.title != PLACEHOLDER:
        claims.append((f"task:{index}:title", task.title))
    if task.assignee != PLACEHOLDER:
        claims.append((f"task:{index}:assignee", task.assignee))
    if task.due != PLACEHOLDER:
        claims.append((f"task:{index}:due", task.due))
    return claims


def _unconfirmed_reason(prepared: str, text: str) -> str | None:
    needle = _normalize(text).rstrip(".,;:!?")
    hits: set[_Hit] = set()
    for window in _windows(prepared, needle):
        hits.update(_classify_window(window, needle))
    if "attack" in hits:
        return _REASON_ATTACK
    if "confirm" in hits and "negate" in hits:
        return _REASON_CONFLICT
    if "confirm" in hits:
        return None
    if "discuss" in hits:
        return _REASON_DISCUSSION
    return _REASON_NO_MARKER


def _windows(prepared: str, needle: str) -> tuple[str, ...]:
    if not needle:
        return ()
    return tuple(
        clause for clause in _CLAUSE.split(prepared) if needle in clause
    )


def _classify_window(window: str, needle: str) -> set[_Hit]:
    hits: set[_Hit] = set()
    if _has_any(window, _ATTACK):
        hits.add("attack")
    if _has_any(window, _NEGATE) or f"не {needle}" in window:
        hits.add("negate")
    remainder = window
    for phrase in _NEGATE:
        remainder = remainder.replace(phrase, " ")
    if _has_any(remainder, _CONFIRM):
        hits.add("confirm")
    if _has_any(window, _DISCUSS):
        hits.add("discuss")
    return hits or {"mention"}


def _has_any(window: str, markers: tuple[str, ...]) -> bool:
    return any(marker in window for marker in markers)


def _prepare(material: str) -> str:
    return _normalize(_SPEAKER.sub("", material))


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())
