"""Определяет неизменяемые контракты классификатора."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Метаданные одного валидного скилла без тела.

    Attributes:
        name: каноническое имя режима в снимке.
        description: описание границы выбора, без инструкций тела.
    """

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class Classification:
    """Исход выбора режима по снимку метаданных.

    Attributes:
        skill: имя из allowlist снимка или отсутствие выбора.
    """

    skill: str | None
