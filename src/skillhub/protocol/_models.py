"""Определяет неизменяемую нормативную модель протокола."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from skillhub.protocol._constants import GRAMMAR_VERSION

_TABLE_DELIMITER = "|"
_PIPE_IN_TASK_FIELD = "поле задачи не должно содержать вертикальную черту"


class ProtocolTask(BaseModel):
    """Описывает одну задачу протокола без вычисления срока.

    Attributes:
        title: формулировка задачи.
        assignee: ответственный или заполнитель.
        due: срок как исходная строка или заполнитель.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    assignee: str
    due: str

    @field_validator("title", "assignee", "due")
    @classmethod
    def reject_table_delimiter(cls, value: str) -> str:
        """Проверяет отсутствие вертикальной черты в поле задачи.

        Args:
            value: проверяемое текстовое поле задачи.

        Returns:
            Значение без символа колонки таблицы.

        Raises:
            ValueError: поле содержит вертикальную черту.
        """
        if _TABLE_DELIMITER in value:
            raise ValueError(_PIPE_IN_TASK_FIELD)
        return value


class Protocol(BaseModel):
    """Описывает протокол встречи с обязательными четырьмя разделами.

    Attributes:
        title: тема встречи.
        date: дата встречи или заполнитель.
        participants: известные имена участников.
        discussion: пункты обсуждения.
        decisions: принятые решения как обычные строки.
        tasks: задачи протокола.
        open_questions: открытые вопросы.
        grammar_version: версия контракта грамматики.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    date: str
    participants: tuple[str, ...]
    discussion: tuple[str, ...]
    decisions: tuple[str, ...]
    tasks: tuple[ProtocolTask, ...]
    open_questions: tuple[str, ...]
    grammar_version: str = GRAMMAR_VERSION


class Clarification(BaseModel):
    """Описывает один вопрос к пропущенному полю протокола.

    Attributes:
        id: стабильный идентификатор уточнения.
        target: целевое поле, которое можно записать ответом.
        reason: причина вопроса.
        hint: подсказка для ответа.
        status: состояние строки уточнения.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    target: str
    reason: str
    hint: str
    status: Literal["pending", "answered", "skipped"]
