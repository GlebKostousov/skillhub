"""Держит operation и skill текущего вызова модели."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_OPERATION: ContextVar[str] = ContextVar("usage_operation", default="unspecified")
_SKILL: ContextVar[str | None] = ContextVar("usage_skill", default=None)


@contextmanager
def bind_call(*, operation: str, skill: str | None = None) -> Iterator[None]:
    """Привязывает операцию и режим к текущему потоку.

    Args:
        operation: имя операции вызова модели.
        skill: выбранный режим или его отсутствие.
    """
    operation_token = _OPERATION.set(operation)
    skill_token = _SKILL.set(skill)
    try:
        yield
    finally:
        _OPERATION.reset(operation_token)
        _SKILL.reset(skill_token)


def bound_call() -> tuple[str, str | None]:
    """Возвращает текущую привязку вызова.

    Returns:
        Операция и режим; без bind — unspecified и отсутствие режима.
    """
    return _OPERATION.get(), _SKILL.get()
