"""Проверяет публичный шов уточнений нормативного протокола."""

from collections.abc import Mapping

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from skillhub.core import SkillHubError
from skillhub.protocol import (
    MAX_CLARIFICATIONS,
    PLACEHOLDER,
    Clarification,
    EmptyClarificationAnswerError,
    ExtraClarificationFieldError,
    InvalidClarificationAnswerError,
    Protocol,
    ProtocolTask,
    UnknownClarificationIdError,
    UnresolvedClarificationError,
    apply_answers,
    build_clarifications,
)


def _protocol(**overrides: object) -> Protocol:
    """Собирает протокол с заполненными косметическими полями."""
    payload: dict[str, object] = {
        "title": "Тема",
        "date": "2026-09-12",
        "participants": ("Анна",),
        "discussion": ("пункт обсуждения",),
        "decisions": ("решение",),
        "tasks": (),
        "open_questions": (),
    }
    payload.update(overrides)
    return Protocol.model_validate(payload)


def _task(
    title: str = "Подготовить отчёт",
    assignee: str = "Анна",
    due: str = "2026-09-15",
) -> ProtocolTask:
    """Собирает задачу протокола."""
    return ProtocolTask(title=title, assignee=assignee, due=due)


def test_same_protocol_builds_identical_clarifications() -> None:
    """Проверяет, что одинаковый протокол даёт одинаковый список вопросов."""
    protocol = _protocol(
        date=PLACEHOLDER,
        participants=(),
        tasks=(
            _task(assignee=PLACEHOLDER, due=PLACEHOLDER),
            _task(title="Вторая задача", due="до следующей встречи"),
        ),
    )

    first = build_clarifications(protocol)
    second = build_clarifications(protocol)

    assert first == second
    assert [item.id for item in first] == [
        "date",
        "participants",
        "task:0:assignee",
        "task:0:due",
    ]
    assert all(item.status == "pending" for item in first)
    assert all(item.target == item.id for item in first)
    assert all(item.reason for item in first)
    assert all(item.hint for item in first)


def test_placeholder_participants_tuple_and_single_placeholder_match() -> None:
    """Проверяет вопрос к участникам для пустого кортежа и одного заполнителя."""
    empty = build_clarifications(_protocol(participants=()))
    single = build_clarifications(_protocol(participants=(PLACEHOLDER,)))

    assert [item.id for item in empty] == ["participants"]
    assert [item.id for item in single] == ["participants"]


def test_cosmetic_gaps_do_not_create_clarifications() -> None:
    """Проверяет отсутствие вопросов к теме, разделам и названию задачи."""
    protocol = _protocol(
        title=PLACEHOLDER,
        discussion=(),
        decisions=(),
        open_questions=(),
        tasks=(_task(title="Срок без пропуска", due="до следующей встречи"),),
    )

    assert build_clarifications(protocol) == ()


def test_priority_puts_open_questions_before_task_gaps() -> None:
    """Проверяет порядок дата → участники → открытые вопросы → задачи."""
    items = build_clarifications(
        _protocol(
            date=PLACEHOLDER,
            participants=(),
            open_questions=("Кто ведёт релиз?", "Нужен ли созвон?"),
            tasks=(_task(assignee=PLACEHOLDER, due=PLACEHOLDER),),
        )
    )

    assert [item.id for item in items] == [
        "date",
        "participants",
        "question:0",
        "question:1",
        "task:0:assignee",
        "task:0:due",
    ]


def test_priority_and_limit_keep_first_important_gaps() -> None:
    """Проверяет лимит списка при длинном наборе пропусков."""
    tasks = tuple(
        _task(title=f"Задача {index}", assignee=PLACEHOLDER, due=PLACEHOLDER)
        for index in range(12)
    )
    items = build_clarifications(
        _protocol(
            date=PLACEHOLDER,
            participants=(),
            open_questions=("Первый вопрос", "Второй вопрос"),
            tasks=tasks,
        )
    )

    assert len(items) == MAX_CLARIFICATIONS
    assert [item.id for item in items[:4]] == [
        "date",
        "participants",
        "question:0",
        "question:1",
    ]


def test_apply_answer_changes_only_target_field() -> None:
    """Проверяет, что ответ меняет только целевое поле."""
    protocol = _protocol(
        date=PLACEHOLDER,
        participants=(),
        tasks=(_task(assignee=PLACEHOLDER, due=PLACEHOLDER),),
    )

    updated = apply_answers(
        protocol,
        ({"id": "date", "action": "answer", "value": "2026-10-01"},),
    )

    assert updated.date == "2026-10-01"
    assert updated.participants == protocol.participants
    assert updated.tasks == protocol.tasks
    assert updated.title == protocol.title
    assert updated.discussion == protocol.discussion
    assert updated.decisions == protocol.decisions
    assert updated.open_questions == protocol.open_questions


def test_apply_open_question_replaces_only_that_item() -> None:
    """Проверяет, что ответ записывается только в выбранный открытый вопрос."""
    protocol = _protocol(open_questions=("Кто ведёт релиз?", "Нужен ли созвон?"))

    updated = apply_answers(
        protocol,
        ({"id": "question:0", "action": "answer", "value": "Марина"},),
    )

    assert updated.open_questions == ("Марина", "Нужен ли созвон?")
    assert updated.date == protocol.date
    assert updated.tasks == protocol.tasks


def test_apply_participants_uses_comma_split_rule() -> None:
    """Проверяет разбор имён участников через запятую по правилу заголовка."""
    protocol = _protocol(participants=())

    updated = apply_answers(
        protocol,
        ({"id": "participants", "action": "answer", "value": "Анна, Борис"},),
    )
    placeholder = apply_answers(
        protocol,
        ({"id": "participants", "action": "answer", "value": PLACEHOLDER},),
    )

    assert updated.participants == ("Анна", "Борис")
    assert placeholder.participants == ()


def test_skip_keeps_placeholder_and_does_not_require_value() -> None:
    """Проверяет, что пропуск не требует текста и не меняет поле."""
    protocol = _protocol(date=PLACEHOLDER, participants=())

    updated = apply_answers(protocol, ({"id": "date", "action": "skip"},))

    assert updated.date == PLACEHOLDER
    assert updated.participants == ()


def test_partial_apply_allows_one_row() -> None:
    """Проверяет частичное применение одной строки при других пропусках."""
    protocol = _protocol(
        date=PLACEHOLDER,
        tasks=(_task(assignee=PLACEHOLDER, due=PLACEHOLDER),),
    )

    updated = apply_answers(
        protocol,
        ({"id": "task:0:assignee", "action": "answer", "value": "Борис"},),
    )

    assert updated.tasks[0].assignee == "Борис"
    assert updated.tasks[0].due == PLACEHOLDER
    assert updated.date == PLACEHOLDER


def test_empty_or_whitespace_answer_is_rejected() -> None:
    """Проверяет отказ пустого и пробельного ответа."""
    protocol = _protocol(date=PLACEHOLDER)

    for value in ("", "   ", "\t"):
        with pytest.raises(EmptyClarificationAnswerError) as caught:
            apply_answers(
                protocol,
                ({"id": "date", "action": "answer", "value": value},),
            )
        assert isinstance(caught.value, SkillHubError)
        assert "   " not in caught.value.public_message
        assert "\t" not in caught.value.public_message
        assert str(caught.value) == caught.value.code


def test_blank_participant_name_is_rejected() -> None:
    """Проверяет отказ ответа с пустым именем в списке участников."""
    protocol = _protocol(participants=())

    with pytest.raises(EmptyClarificationAnswerError):
        apply_answers(
            protocol,
            ({"id": "participants", "action": "answer", "value": "Анна,"},),
        )


def test_require_resolved_rejects_missing_pending_id() -> None:
    """Проверяет блок незакрытого уточнения без HTTP."""
    protocol = _protocol(date=PLACEHOLDER, participants=())

    with pytest.raises(UnresolvedClarificationError) as caught:
        apply_answers(
            protocol,
            ({"id": "date", "action": "skip"},),
            require_resolved=True,
        )

    assert isinstance(caught.value, SkillHubError)
    assert "participants" not in caught.value.public_message
    assert caught.value.status_code == 409


def test_require_resolved_accepts_answer_and_skip_coverage() -> None:
    """Проверяет полное применение, когда все идентификаторы закрыты."""
    protocol = _protocol(date=PLACEHOLDER, participants=())

    updated = apply_answers(
        protocol,
        (
            {"id": "date", "action": "answer", "value": "2026-10-02"},
            {"id": "participants", "action": "skip"},
        ),
        require_resolved=True,
    )

    assert updated.date == "2026-10-02"
    assert updated.participants == ()


def test_foreign_identifier_injection_leaves_protocol_unchanged() -> None:
    """Проверяет отказ чужого идентификатора как вектора записи чужого поля."""
    protocol = _protocol(date=PLACEHOLDER, tasks=(_task(),))
    attacks = (
        "title",
        "discussion",
        "decisions",
        "open_questions",
        "task:0:title",
        "grammar_version",
        "task:0:assignee",
        "__dict__",
    )

    for identifier in attacks:
        with pytest.raises(UnknownClarificationIdError) as caught:
            apply_answers(
                protocol,
                ({"id": identifier, "action": "answer", "value": "подмена"},),
            )
        assert isinstance(caught.value, SkillHubError)
        assert identifier not in caught.value.public_message
        assert "подмена" not in caught.value.public_message
        assert apply_answers(protocol, ()) == protocol


def test_invalid_action_is_rejected() -> None:
    """Проверяет отказ решения с неизвестным действием."""
    protocol = _protocol(date=PLACEHOLDER)

    with pytest.raises(ExtraClarificationFieldError):
        apply_answers(protocol, ({"id": "date", "action": "cancel"},))


def test_extra_field_write_is_rejected() -> None:
    """Проверяет отказ лишнего поля решения как вектора скрытой записи."""
    protocol = _protocol(date=PLACEHOLDER)
    decision: dict[str, object] = {
        "id": "date",
        "action": "answer",
        "value": "2026-10-03",
        "title": "скрытая тема",
    }

    with pytest.raises(ExtraClarificationFieldError) as caught:
        apply_answers(protocol, (decision,))

    assert isinstance(caught.value, SkillHubError)
    assert "title" not in caught.value.public_message
    assert "скрытая тема" not in caught.value.public_message


def test_pipe_in_task_answer_is_rejected_by_task_validator() -> None:
    """Проверяет отказ вертикальной черты в ответе ответственного."""
    protocol = _protocol(tasks=(_task(assignee=PLACEHOLDER),))
    raw_answer = "Анна|Борис"

    with pytest.raises(InvalidClarificationAnswerError) as caught:
        apply_answers(
            protocol,
            ({"id": "task:0:assignee", "action": "answer", "value": raw_answer},),
        )

    assert isinstance(caught.value, SkillHubError)
    assert "|" not in caught.value.public_message
    assert raw_answer not in caught.value.public_message
    assert "|" not in str(caught.value)
    assert raw_answer not in str(caught.value)
    assert apply_answers(protocol, ()) == protocol


def test_clarification_model_forbids_extra_and_is_frozen() -> None:
    """Проверяет неизменяемость уточнения и запрет лишнего поля."""
    item = Clarification(
        id="date",
        target="date",
        reason="Какого числа была встреча?",
        hint="Например, 13.09.2026",
        status="pending",
    )

    with pytest.raises(ValidationError):
        item.status = "answered"
    with pytest.raises(ValidationError):
        Clarification.model_validate({**item.model_dump(), "notes": "лишнее"})


_SAFE = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters=" -.",
    blacklist_characters="|#\n\r,",
)
_TOKEN = (
    st.text(_SAFE, min_size=1, max_size=16)
    .map(str.strip)
    .filter(lambda token: token not in {"", PLACEHOLDER})
)


@st.composite
def _gap_protocols(draw: st.DrawFn) -> Protocol:
    task_count = draw(st.integers(min_value=0, max_value=3))
    tasks = tuple(
        _task(
            title=draw(_TOKEN),
            assignee=PLACEHOLDER if draw(st.booleans()) else "Анна",
            due=PLACEHOLDER if draw(st.booleans()) else "2026-09-15",
        )
        for _ in range(task_count)
    )
    return _protocol(
        date=PLACEHOLDER if draw(st.booleans()) else "2026-09-12",
        participants=() if draw(st.booleans()) else ("Анна",),
        tasks=tasks,
    )


def _decision_for(
    item: Clarification,
    action: str,
    value: str,
) -> dict[str, object]:
    if action == "skip":
        return {"id": item.id, "action": "skip"}
    return {"id": item.id, "action": "answer", "value": value}


def _chosen_value(
    decisions: tuple[Mapping[str, object], ...],
    identifier: str,
) -> str | None:
    for decision in decisions:
        if decision["id"] == identifier and decision["action"] == "answer":
            return str(decision["value"])
    return None


@given(protocol=_gap_protocols(), data=st.data())
@settings(max_examples=40)
def test_answer_skip_combinations_do_not_change_other_fields(
    protocol: Protocol,
    data: st.DataObject,
) -> None:
    """Проверяет, что комбинации ответа и пропуска не меняют чужие поля."""
    items = build_clarifications(protocol)
    decisions = tuple(
        _decision_for(
            item,
            data.draw(st.sampled_from(("answer", "skip"))),
            "Борис, Виктор" if item.id == "participants" else data.draw(_TOKEN),
        )
        for item in items
    )

    updated = apply_answers(protocol, decisions)

    assert updated.title == protocol.title
    assert updated.discussion == protocol.discussion
    assert updated.decisions == protocol.decisions
    assert updated.grammar_version == protocol.grammar_version
    for index, question in enumerate(protocol.open_questions):
        chosen = _chosen_value(decisions, f"question:{index}")
        assert updated.open_questions[index] == (chosen or question)
    assert updated.date == (_chosen_value(decisions, "date") or protocol.date)
    participants_value = _chosen_value(decisions, "participants")
    if participants_value is None:
        assert updated.participants == protocol.participants
    else:
        assert updated.participants == ("Борис", "Виктор")
    for index, task in enumerate(protocol.tasks):
        assignee = _chosen_value(decisions, f"task:{index}:assignee")
        due = _chosen_value(decisions, f"task:{index}:due")
        assert updated.tasks[index].title == task.title
        assert updated.tasks[index].assignee == (assignee or task.assignee)
        assert updated.tasks[index].due == (due or task.due)
