"""Разрешает только четыре простых режима в текстовый обработчик."""

from skillhub.assistant._handler import SkillHandler

_SIMPLE_SKILL_NAMES = frozenset(
    {
        "business-message",
        "meeting-action-items",
        "text-summary",
        "text-translation",
    }
)


class SkillHandlerRegistry:
    """Сопоставляет имя скилла разрешённому обработчику."""

    def __init__(self, handler: SkillHandler) -> None:
        """Сохраняет общий обработчик простых режимов.

        Args:
            handler: доверенный текстовый обработчик.
        """
        self._handler = handler

    def resolve(self, name: str) -> SkillHandler | None:
        """Возвращает обработчик только для четырёх простых имён.

        Args:
            name: каноническое имя выбранного режима.

        Returns:
            Общий текстовый обработчик или отсутствие обработчика.
        """
        if name in _SIMPLE_SKILL_NAMES:
            return self._handler
        return None
