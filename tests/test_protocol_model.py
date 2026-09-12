"""Проверяет публичный фасад нормативной модели протокола."""

import pytest
from pydantic import ValidationError

import skillhub.protocol as protocol_facade
from skillhub.core import SkillHubError
from skillhub.protocol import (
    GRAMMAR_VERSION,
    H1_PREFIX,
    MAX_CLARIFICATIONS,
    PLACEHOLDER,
    SECTION_TITLES,
    Clarification,
    EmptyClarificationAnswerError,
    ExtraClarificationFieldError,
    Protocol,
    ProtocolParseError,
    ProtocolTask,
    UnknownClarificationIdError,
    UnresolvedClarificationError,
    apply_answers,
    build_clarifications,
)


def test_facade_exposes_fixed_grammar_constants() -> None:
    """Проверяет фиксированные константы контракта грамматики v1."""
    assert GRAMMAR_VERSION == "v1"
    assert PLACEHOLDER == "\u2014"
    assert H1_PREFIX == "Протокол встречи:"
    assert SECTION_TITLES == (
        "Обсуждение",
        "Решения",
        "Задачи",
        "Открытые вопросы",
    )


def test_normative_protocol_assembles_and_equals_itself() -> None:
    """Проверяет сборку нормативного протокола и равенство самому себе."""
    protocol = Protocol(
        title="Подготовка квартального отчёта",
        date="2026-09-12",
        participants=("Анна", "Борис"),
        discussion=(
            "Команда сверила состав квартального отчёта и доступность исходных данных.",
        ),
        decisions=("Включить в отчёт показатели продаж и поддержки.",),
        tasks=(
            ProtocolTask(
                title="Проверить показатели продаж",
                assignee="Анна",
                due="2026-09-15",
            ),
            ProtocolTask(
                title="Подготовить данные поддержки",
                assignee=PLACEHOLDER,
                due="до следующей встречи",
            ),
        ),
        open_questions=("Нужно определить ответственного за данные поддержки.",),
    )

    assert protocol.title == "Подготовка квартального отчёта"
    assert protocol.date == "2026-09-12"
    assert protocol.participants == ("Анна", "Борис")
    assert protocol.discussion == (
        "Команда сверила состав квартального отчёта и доступность исходных данных.",
    )
    assert protocol.decisions == ("Включить в отчёт показатели продаж и поддержки.",)
    assert protocol.tasks[0].title == "Проверить показатели продаж"
    assert protocol.tasks[0].assignee == "Анна"
    assert protocol.tasks[0].due == "2026-09-15"
    assert protocol.tasks[1].assignee == PLACEHOLDER
    assert protocol.tasks[1].due == "до следующей встречи"
    assert protocol.open_questions == (
        "Нужно определить ответственного за данные поддержки.",
    )
    assert protocol.grammar_version == GRAMMAR_VERSION
    assert protocol == Protocol.model_validate(protocol.model_dump())


def _empty_protocol_payload() -> dict[str, object]:
    """Собирает минимальный допустимый набор полей протокола."""
    return {
        "title": "Тема",
        "date": PLACEHOLDER,
        "participants": (),
        "discussion": (),
        "decisions": (),
        "tasks": (),
        "open_questions": (),
    }


def test_extra_section_is_rejected() -> None:
    """Проверяет отказ конструктора при лишнем разделе."""
    payload = _empty_protocol_payload()
    payload["notes"] = ("лишний раздел",)

    with pytest.raises(ValidationError):
        Protocol.model_validate(payload)


@pytest.mark.parametrize(
    "omitted",
    ["discussion", "decisions", "tasks", "open_questions"],
)
def test_omitted_section_is_rejected(omitted: str) -> None:
    """Проверяет отказ при отсутствии обязательного раздела."""
    payload = _empty_protocol_payload()
    del payload[omitted]

    with pytest.raises(ValidationError):
        Protocol.model_validate(payload)


def test_empty_sections_are_empty_sequences() -> None:
    """Проверяет хранение пустого раздела как пустой последовательности."""
    protocol = Protocol.model_validate(_empty_protocol_payload())

    assert protocol.participants == ()
    assert protocol.discussion == ()
    assert protocol.decisions == ()
    assert protocol.tasks == ()
    assert protocol.open_questions == ()
    assert protocol.date == PLACEHOLDER
    assert protocol.grammar_version == GRAMMAR_VERSION
    assert None not in (
        protocol.discussion,
        protocol.decisions,
        protocol.tasks,
        protocol.open_questions,
    )
    assert "не указано" not in protocol.model_dump().values()


def test_none_section_is_rejected() -> None:
    """Проверяет отказ при подмене раздела значением None."""
    payload = _empty_protocol_payload()
    payload["discussion"] = None

    with pytest.raises(ValidationError):
        Protocol.model_validate(payload)


def test_protocol_and_task_are_frozen() -> None:
    """Проверяет неизменяемость собранных значений протокола."""
    protocol = Protocol.model_validate(_empty_protocol_payload())
    task = ProtocolTask(title="Задача", assignee=PLACEHOLDER, due=PLACEHOLDER)

    with pytest.raises(ValidationError):
        protocol.title = "Другая тема"
    with pytest.raises(ValidationError):
        task.due = "2026-09-15"


def test_protocol_parse_error_collects_structural_fields() -> None:
    """Проверяет сбор координат структурного отказа без разбора текста."""
    error = ProtocolParseError(line=14, expected="## Обсуждение", got="## Итоги")

    assert isinstance(error, SkillHubError)
    assert error.line == 14
    assert error.expected == "## Обсуждение"
    assert error.got == "## Итоги"
    assert error.code == "protocol_parse_error"
    assert error.status_code == 422
    assert error.public_message == "Структура протокола нарушена."
    assert str(error) == "protocol_parse_error"
    assert error.got not in error.public_message
    assert not hasattr(error, "parse")


def test_extra_task_field_is_rejected() -> None:
    """Проверяет отказ задачи при лишнем поле конструктора."""
    with pytest.raises(ValidationError):
        ProtocolTask.model_validate(
            {
                "title": "Задача",
                "assignee": PLACEHOLDER,
                "due": PLACEHOLDER,
                "priority": "высокий",
            }
        )


def test_facade_exports_parse_and_render() -> None:
    """Проверяет наличие разбора и воспроизведения на публичном фасаде."""
    assert "parse" in protocol_facade.__all__
    assert "render" in protocol_facade.__all__
    assert hasattr(protocol_facade, "parse")
    assert hasattr(protocol_facade, "render")


def test_facade_exports_clarification_seam() -> None:
    """Проверяет наличие шва уточнений на публичном фасаде."""
    assert MAX_CLARIFICATIONS == 10
    assert "Clarification" in protocol_facade.__all__
    assert "build_clarifications" in protocol_facade.__all__
    assert "apply_answers" in protocol_facade.__all__
    assert build_clarifications is protocol_facade.build_clarifications
    assert apply_answers is protocol_facade.apply_answers
    assert Clarification is protocol_facade.Clarification


@pytest.mark.parametrize(
    ("error_type", "code", "status_code"),
    [
        (EmptyClarificationAnswerError, "empty_clarification_answer", 422),
        (UnknownClarificationIdError, "unknown_clarification_id", 422),
        (ExtraClarificationFieldError, "extra_clarification_field", 422),
        (UnresolvedClarificationError, "unresolved_clarification", 409),
    ],
)
def test_clarification_errors_hide_user_content(
    error_type: type[SkillHubError],
    code: str,
    status_code: int,
) -> None:
    """Проверяет типизированные ошибки уточнений без пользовательского содержимого."""
    error = error_type()

    assert isinstance(error, SkillHubError)
    assert error.code == code
    assert error.status_code == status_code
    assert str(error) == code
    assert error.public_message
    assert "подмена" not in error.public_message
