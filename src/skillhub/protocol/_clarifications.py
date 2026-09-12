# ruff: noqa: RUF001
"""Строит уточнения протокола и применяет ответы к целевым полям."""

from collections.abc import Iterator, Mapping, Sequence

from pydantic import ValidationError

from skillhub.protocol._constants import MAX_CLARIFICATIONS, PLACEHOLDER
from skillhub.protocol._errors import (
    EmptyClarificationAnswerError,
    ExtraClarificationFieldError,
    InvalidClarificationAnswerError,
    UnknownClarificationIdError,
    UnresolvedClarificationError,
)
from skillhub.protocol._models import Clarification, Protocol, ProtocolTask

_ANSWER = "answer"
_SKIP = "skip"
_ALLOWED_KEYS = frozenset({"id", "action", "value"})
_DATE_REASON = "Дата встречи не указана."
_DATE_HINT = "Укажите дату встречи."
_PARTICIPANTS_REASON = "Участники встречи не указаны."
_PARTICIPANTS_HINT = "Перечислите имена через запятую."
_ASSIGNEE_HINT = "Укажите ответственного."
_DUE_HINT = "Укажите срок."

type _ParsedDecision = tuple[str, str, str | None]


def build_clarifications(protocol: Protocol) -> tuple[Clarification, ...]:
    """Возвращает конечный список уточнений по пропускам протокола.

    Args:
        protocol: нормативный протокол встречи.

    Returns:
        Уточнения в порядке приоритета, не длиннее лимита.
    """
    return tuple(_iter_gaps(protocol))[:MAX_CLARIFICATIONS]


def apply_answers(
    protocol: Protocol,
    decisions: Sequence[Mapping[str, object]],
    *,
    require_resolved: bool = False,
) -> Protocol:
    """Применяет решения только к целевым полям уточнений.

    Args:
        protocol: исходный протокол.
        decisions: решения с идентификатором и действием.
        require_resolved: отклоняет набор с незакрытыми уточнениями.

    Returns:
        Новый протокол с применёнными ответами.

    Raises:
        EmptyClarificationAnswerError: ответ пустой или пробельный.
        ExtraClarificationFieldError: решение содержит лишнее поле.
        InvalidClarificationAnswerError: ответ нельзя записать в поле задачи.
        UnknownClarificationIdError: идентификатор не входит в список.
        UnresolvedClarificationError: при полном применении остался пропуск.
    """
    allowed = {item.id for item in build_clarifications(protocol)}
    parsed = tuple(_parse_decision(raw) for raw in decisions)
    _reject_unknown(parsed, allowed)
    if require_resolved and allowed - {item[0] for item in parsed}:
        raise UnresolvedClarificationError
    return _fold_answers(protocol, parsed)


def _iter_gaps(protocol: Protocol) -> Iterator[Clarification]:
    if protocol.date == PLACEHOLDER:
        yield _item("date", _DATE_REASON, _DATE_HINT)
    if _participants_missing(protocol.participants):
        yield _item("participants", _PARTICIPANTS_REASON, _PARTICIPANTS_HINT)
    for index, task in enumerate(protocol.tasks):
        yield from _task_gaps(index, task)


def _task_gaps(index: int, task: ProtocolTask) -> Iterator[Clarification]:
    if task.assignee == PLACEHOLDER:
        yield _item(
            f"task:{index}:assignee",
            f"Не указан ответственный за задачу «{task.title}».",
            _ASSIGNEE_HINT,
        )
    if task.due == PLACEHOLDER:
        yield _item(
            f"task:{index}:due",
            f"Не указан срок задачи «{task.title}».",
            _DUE_HINT,
        )


def _participants_missing(participants: tuple[str, ...]) -> bool:
    return participants in {(), (PLACEHOLDER,)}


def _item(target: str, reason: str, hint: str) -> Clarification:
    return Clarification(
        id=target,
        target=target,
        reason=reason,
        hint=hint,
        status="pending",
    )


def _parse_decision(raw: Mapping[str, object]) -> _ParsedDecision:
    if set(raw) - _ALLOWED_KEYS:
        raise ExtraClarificationFieldError
    identifier = raw.get("id")
    action = raw.get("action")
    if not isinstance(identifier, str) or action not in {_ANSWER, _SKIP}:
        raise ExtraClarificationFieldError
    if action == _SKIP:
        return identifier, action, None
    value = raw.get("value")
    if not isinstance(value, str) or not value.strip():
        raise EmptyClarificationAnswerError
    return identifier, action, value.strip()


def _reject_unknown(parsed: tuple[_ParsedDecision, ...], allowed: set[str]) -> None:
    if any(identifier not in allowed for identifier, _action, _value in parsed):
        raise UnknownClarificationIdError


def _fold_answers(protocol: Protocol, parsed: tuple[_ParsedDecision, ...]) -> Protocol:
    current = protocol
    for identifier, action, value in parsed:
        if action == _SKIP or value is None:
            continue
        current = _write_field(current, identifier, value)
    return current


def _write_field(protocol: Protocol, identifier: str, value: str) -> Protocol:
    if identifier == "date":
        return protocol.model_copy(update={"date": value})
    if identifier == "participants":
        return protocol.model_copy(update={"participants": _split_names(value)})
    index_text, field = identifier.removeprefix("task:").split(":", 1)
    tasks = list(protocol.tasks)
    index = int(index_text)
    payload = tasks[index].model_dump()
    payload[field] = value
    try:
        tasks[index] = ProtocolTask.model_validate(payload)
    except ValidationError:
        raise InvalidClarificationAnswerError from None
    return protocol.model_copy(update={"tasks": tuple(tasks)})


def _split_names(raw: str) -> tuple[str, ...]:
    if raw == PLACEHOLDER:
        return ()
    names = tuple(part.strip() for part in raw.split(","))
    if any(not name for name in names):
        raise EmptyClarificationAnswerError
    return names
