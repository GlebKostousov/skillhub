"""Определяет неизменяемые значения файлового реестра."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skill:
    """Неизменяемые проверенные данные одного скилла.

    Attributes:
        name: каноническое имя в kebab-case.
        caption: человекочитаемая подпись режима.
        description: описание границы выбора режима.
        body: непустые инструкции Markdown.
        has_files: признак дополнительных обычных файлов внутри каталога.
    """

    name: str
    caption: str
    description: str
    body: str
    has_files: bool


@dataclass(frozen=True, slots=True)
class LoadIssue:
    """Неизменяемая безопасная причина пропуска одного каталога.

    Attributes:
        path: относительный безопасный идентификатор каталога.
        reason: стабильный машинный код причины.
    """

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class LoadReport:
    """Неизменяемый результат одной полной загрузки.

    Attributes:
        skills: проверенные скиллы в детерминированном порядке.
        issues: причины пропуска в детерминированном порядке.
    """

    skills: tuple[Skill, ...]
    issues: tuple[LoadIssue, ...]

    @property
    def loaded(self) -> int:
        """Возвращает число загруженных скиллов.

        Returns:
            Размер неизменяемого набора скиллов.
        """
        return len(self.skills)

    @property
    def skipped(self) -> int:
        """Возвращает число пропущенных элементов.

        Returns:
            Размер неизменяемого набора причин.
        """
        return len(self.issues)

    def search(self, query: str) -> tuple[Skill, ...]:
        """Ищет по данным именно этой загрузки.

        Args:
            query: искомая часть имени или подписи.

        Returns:
            Подходящие скиллы в порядке канонического имени.
        """
        return _search_skills(self.skills, query)


def _search_skills(skills: Iterable[Skill], query: str) -> tuple[Skill, ...]:
    needle = query.casefold()
    return tuple(
        skill
        for skill in sorted(skills, key=lambda item: item.name)
        if needle in skill.name.casefold() or needle in skill.caption.casefold()
    )
