"""Определяет неизменяемую нормативную модель протокола."""

from pydantic import BaseModel, ConfigDict

from skillhub.protocol._constants import GRAMMAR_VERSION


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
