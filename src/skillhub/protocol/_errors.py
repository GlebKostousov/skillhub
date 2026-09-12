"""Определяет типизированную ошибку разбора структуры протокола."""

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
