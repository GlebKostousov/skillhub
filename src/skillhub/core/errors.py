"""Определяет безопасные типизированные ошибки SkillHub."""

from typing import ClassVar


class SkillHubError(Exception):
    """Базовая ожидаемая ошибка без пользовательского содержимого.

    Attributes:
        code: стабильный машинный код.
        status_code: HTTP-статус для транспортного слоя.
        public_message: безопасное сообщение для клиента.
    """

    code: ClassVar[str] = "skillhub_error"
    status_code: ClassVar[int] = 400
    public_message: ClassVar[str] = "Запрос не выполнен."

    def __init__(self) -> None:
        """Создаёт ошибку со стабильным безопасным описанием."""
        super().__init__(self.code)
