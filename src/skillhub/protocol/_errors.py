"""Определяет типизированные ошибки разбора и генерации протокола."""

from typing import NoReturn

from skillhub.core import SkillHubError


class ProtocolParseError(SkillHubError):
    """Описывает отказ разбора одной структурной строки протокола.

    Attributes:
        line: номер строки исходного текста.
        expected: ожидаемый структурный элемент.
        got: фактически встреченный фрагмент.
    """

    code = "protocol_parse_error"
    status_code = 422
    public_message = "Структура протокола нарушена."

    def __init__(self, line: int, expected: str, got: str) -> None:
        """Сохраняет координаты структурного отказа без записи содержимого.

        Args:
            line: номер строки исходного текста.
            expected: ожидаемый структурный элемент.
            got: фактически встреченный фрагмент.
        """
        self.line = line
        self.expected = expected
        self.got = got
        super().__init__()


class ProtocolGenerationError(SkillHubError):
    """Описывает отказ принять незавершённый ответ порта генерации."""

    code = "protocol_generation_error"
    status_code = 422
    public_message = "Ответ генерации протокола не завершён."


class EmptyClarificationAnswerError(SkillHubError):
    """Описывает отказ принять пустой или пробельный ответ."""

    code = "empty_clarification_answer"
    status_code = 422
    public_message = "Ответ на уточнение не должен быть пустым."


class UnknownClarificationIdError(SkillHubError):
    """Описывает отказ при чужом идентификаторе уточнения."""

    code = "unknown_clarification_id"
    status_code = 422
    public_message = "Идентификатор уточнения не найден."


class ExtraClarificationFieldError(SkillHubError):
    """Описывает отказ решения с лишним или недопустимым полем."""

    code = "extra_clarification_field"
    status_code = 422
    public_message = "Решение содержит недопустимое поле."


class InvalidClarificationAnswerError(SkillHubError):
    """Описывает отказ ответа, который нельзя записать в поле задачи."""

    code = "invalid_clarification_answer"
    status_code = 422
    public_message = "Ответ на уточнение содержит недопустимые символы."


class UnresolvedClarificationError(SkillHubError):
    """Описывает отказ полного применения при незакрытом уточнении."""

    code = "unresolved_clarification"
    status_code = 409
    public_message = "Есть незакрытые уточнения."


def reject(line: int, expected: str, got: str) -> NoReturn:
    """Поднимает структурный отказ разбора одной строки.

    Args:
        line: номер строки исходного текста.
        expected: ожидаемый структурный элемент.
        got: фактически встреченный фрагмент.
    """
    raise ProtocolParseError(line, expected, got)
