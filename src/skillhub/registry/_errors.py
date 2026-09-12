"""Определяет внутренние сигналы безопасного отказа реестра."""

from typing import NoReturn


class InvalidSkillError(Exception):
    """Передаёт только стабильную причину ожидаемого пропуска."""

    def __init__(self, reason: str) -> None:
        """Сохраняет машинный код без исходного исключения.

        Args:
            reason: стабильная причина пропуска.
        """
        self.reason = reason
        super().__init__(reason)


class RegistryLimitError(Exception):
    """Обозначает превышение бюджета до обработки корневых записей."""


def reject(reason: str) -> NoReturn:
    """Поднимает безопасный отказ с указанной причиной.

    Args:
        reason: стабильный машинный код.
    """
    raise InvalidSkillError(reason) from None


def reject_registry_limit() -> NoReturn:
    """Поднимает безопасный отказ корневого бюджета."""
    raise RegistryLimitError from None
