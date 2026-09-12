"""Собирает наблюдаемые поверхности отказа без внутренних деталей."""

from collections.abc import Iterable

_KEY = "sk-e6-s1-provider-key-9172"
_PROMPT = "SYSTEM-PROMPT-e6-s1-must-not-leave-the-skill-body"
_ABS_PATH = r"C:\e6-s1-secret\sk-e6-s1-provider-key-9172"


def leak_markers() -> tuple[str, str, str]:
    """Возвращает маркеры ключа, prompt и абсолютного пути."""
    return _KEY, _PROMPT, _ABS_PATH


def observed_text(*parts: object) -> str:
    """Склеивает ответ, журнал и исключение в одну проверяемую строку."""
    return "\n".join(_stringify(part) for part in parts)


def assert_no_secret_leak(*parts: object) -> None:
    """Проверяет отсутствие ключа, prompt и абсолютного пути.

    Args:
        parts: ответ, журнал, исключение или их текстовое представление.
    """
    observed = observed_text(*parts)
    for marker in leak_markers():
        assert marker not in observed


def _stringify(value: object) -> str:
    if isinstance(value, BaseException):
        return _stringify_exception(value)
    if isinstance(value, str):
        return value
    return repr(value)


def _stringify_exception(exc: BaseException) -> str:
    chunks = [repr(exc), str(exc), *(str(arg) for arg in exc.args)]
    chunks.extend(_chained_text(exc))
    return "\n".join(chunks)


def _chained_text(exc: BaseException) -> Iterable[str]:
    if exc.__cause__ is not None:
        yield _stringify_exception(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
        yield _stringify_exception(exc.__context__)
