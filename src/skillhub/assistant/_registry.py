"""Разрешает простые режимы и необязательный обработчик протокола."""

from collections.abc import Mapping

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

    def __init__(
        self,
        handler: SkillHandler,
        extra: Mapping[str, SkillHandler] | None = None,
    ) -> None:
        """Сохраняет общий обработчик и необязательные узкие соответствия.

        Args:
            handler: доверенный текстовый обработчик простых режимов.
            extra: явные обработчики по каноническому имени, например протокол.
        """
        self._handler = handler
        self._extra = dict(extra or {})

    def resolve(self, name: str) -> SkillHandler | None:
        """Возвращает обработчик для простого имени или зарегистрированного extra.

        Args:
            name: каноническое имя выбранного режима.

        Returns:
            Сопоставленный обработчик или отсутствие обработчика.
        """
        extra = self._extra.get(name)
        if extra is not None:
            return extra
        if name in _SIMPLE_SKILL_NAMES:
            return self._handler
        return None
