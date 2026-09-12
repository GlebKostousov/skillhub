"""Реализует безопасные операции через POSIX-дескрипторы."""

import importlib
import os
import stat
import sys
from collections import deque
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from skillhub.registry._platform_types import Handle, UnsafePathError
from skillhub.registry._resolution import ResolutionBudget


class _FcntlModule(Protocol):
    """Описывает используемую часть платформенного модуля fcntl."""

    def fcntl(self, descriptor: int, command: int, argument: bytes) -> bytes:
        """Возвращает результат команды над дескриптором."""
        ...


class _OpenFunction(Protocol):
    """Описывает POSIX-вариант os.open с родительским дескриптором."""

    def __call__(
        self,
        path: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        """Возвращает открытый файловый дескриптор."""
        ...


_fcntl = cast("_FcntlModule", importlib.import_module("fcntl"))
_os_open = cast("_OpenFunction", os.open)
OPEN_FLAGS = (
    os.O_RDONLY
    | cast("int", getattr(os, "O_NOFOLLOW", 0))
    | cast("int", getattr(os, "O_NONBLOCK", 0))
    | cast("int", getattr(os, "O_CLOEXEC", 0))
)


@dataclass(slots=True)
class _ResolutionState:
    """Хранит ограниченное состояние итеративного разрешения пути."""

    parent: Handle
    pending: deque[str]
    budget: ResolutionBudget
    owned_parent: Handle | None = None


def open_path(path: Path) -> Handle:
    """Открывает путь с неблокирующей семантикой.

    Args:
        path: путь открываемого объекта.

    Returns:
        Метаданные фактически открытого объекта.
    """
    return _make_handle(_os_open(path, OPEN_FLAGS))


def open_child(root: Handle, parent: Handle, name: str) -> Handle:
    """Открывает потомка относительно родительского дескриптора.

    Args:
        root: открытый корень разрешённого дерева.
        parent: открытый родительский каталог.
        name: имя непосредственного потомка.

    Returns:
        Метаданные фактически открытого объекта.
    """
    return _resolve_components(root, parent, (name,))


def iter_entries(directory: Handle) -> Iterator[str]:
    """Перечисляет имена через тот же открытый каталог.

    Args:
        directory: открытый каталог.

    Yields:
        Имена непосредственных записей.
    """
    with os.scandir(directory.token) as entries:
        for entry in entries:
            yield entry.name


def read(handle: Handle, size: int) -> bytes:
    """Читает ограниченный объём с текущего дескриптора.

    Args:
        handle: открытый обычный файл.
        size: максимальное число байтов.

    Returns:
        Прочитанные байты.
    """
    return os.read(handle.token, size)


def close(handle: Handle) -> None:
    """Закрывает дескриптор без раскрытия ошибки очистки.

    Args:
        handle: закрываемый объект.
    """
    with suppress(OSError):
        os.close(handle.token)


def _open_plain(parent: Handle, name: str) -> Handle:
    descriptor = _os_open(name, OPEN_FLAGS, dir_fd=parent.token)
    return _make_handle(descriptor)


def _resolve_components(
    root: Handle,
    initial_parent: Handle,
    components: tuple[str, ...],
) -> Handle:
    state = _resolution_state(initial_parent, components)
    try:
        return _resolution_loop(root, state)
    finally:
        _close_owned(state)


def _resolution_loop(root: Handle, state: _ResolutionState) -> Handle:
    while state.pending:
        result = _visit_component(root, state)
        if result is not None:
            return result
    return _make_handle(os.dup(state.parent.token))


def _resolution_state(
    parent: Handle,
    components: tuple[str, ...],
) -> _ResolutionState:
    budget = ResolutionBudget()
    budget.require_pending(len(components))
    return _ResolutionState(parent, deque(components), budget)


def _visit_component(
    root: Handle,
    state: _ResolutionState,
) -> Handle | None:
    name = state.pending.popleft()
    state.budget.visit_component()
    status = os.stat(name, dir_fd=state.parent.token, follow_symlinks=False)
    if stat.S_ISLNK(status.st_mode):
        _follow_link(root, state, name)
        return None
    return _accept_plain_component(state, name)


def _follow_link(
    root: Handle,
    state: _ResolutionState,
    name: str,
) -> None:
    state.budget.follow_link()
    target = Path(os.readlink(name, dir_fd=state.parent.token))
    components = _link_components(target, state.parent, root, tuple(state.pending))
    state.budget.require_pending(len(components))
    state.pending = deque(components)
    _replace_parent(state, root, owned=False)


def _accept_plain_component(
    state: _ResolutionState,
    name: str,
) -> Handle | None:
    handle = _open_plain(state.parent, name)
    if not state.pending:
        return handle
    _require_directory(handle)
    _replace_parent(state, handle, owned=True)
    return None


def _require_directory(handle: Handle) -> None:
    if handle.is_directory:
        return
    close(handle)
    raise UnsafePathError from None


def _replace_parent(
    state: _ResolutionState,
    parent: Handle,
    *,
    owned: bool,
) -> None:
    _close_owned(state)
    state.parent = parent
    state.owned_parent = parent if owned else None


def _close_owned(state: _ResolutionState) -> None:
    if state.owned_parent is not None:
        close(state.owned_parent)
    state.owned_parent = None


def _link_components(
    target: Path,
    parent: Handle,
    root: Handle,
    remaining: tuple[str, ...],
) -> tuple[str, ...]:
    candidate = _absolute_link_target(target, parent.final_path)
    normalized = Path(os.path.normpath(candidate))
    try:
        relative = normalized.relative_to(root.final_path)
    except ValueError:
        raise UnsafePathError from None
    return (*relative.parts, *remaining)


def _absolute_link_target(target: Path, parent: Path) -> Path:
    if target.is_absolute():
        return target
    return parent / target


def _make_handle(descriptor: int) -> Handle:
    try:
        status = os.fstat(descriptor)
        final_path = _final_path(descriptor)
    except OSError:
        os.close(descriptor)
        raise
    return Handle(
        token=descriptor,
        final_path=final_path,
        identity=(status.st_dev, status.st_ino),
        is_directory=stat.S_ISDIR(status.st_mode),
        is_regular=stat.S_ISREG(status.st_mode),
    )


def _final_path(descriptor: int) -> Path:
    if sys.platform == "darwin":
        return _darwin_final_path(descriptor)
    return _fd_link_path(descriptor)


def _darwin_final_path(descriptor: int) -> Path:
    command = cast("int", getattr(_fcntl, "F_GETPATH", 50))
    raw_path = _fcntl.fcntl(descriptor, command, b"\0" * 4_096)
    return Path(os.fsdecode(raw_path.split(b"\0", maxsplit=1)[0]))


def _fd_link_path(descriptor: int) -> Path:
    try:
        return Path(f"/proc/self/fd/{descriptor}").readlink()
    except OSError:
        return Path(f"/dev/fd/{descriptor}").readlink()
