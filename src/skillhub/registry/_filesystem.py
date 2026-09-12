"""Скрывает дескрипторы за семантической файловой границей реестра."""

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from skillhub.registry._errors import (
    InvalidSkillError,
    reject,
    reject_registry_limit,
)
from skillhub.registry._limits import (
    MAX_AUX_DEPTH,
    MAX_AUX_ENTRIES,
    MAX_ROOT_ENTRIES,
    MAX_SKILL_FILE_BYTES,
    MAX_SKILLS,
)
from skillhub.registry._platform_types import (
    Handle,
    PlatformAdapter,
    UnsafePathError,
)

_BACKEND_MODULE = (
    "skillhub.registry._windows_io"
    if os.name == "nt"
    else "skillhub.registry._posix_io"
)
_backend = cast("PlatformAdapter", importlib.import_module(_BACKEND_MODULE))


@dataclass(slots=True)
class _WalkState:
    """Хранит состояние одного ограниченного рекурсивного обхода."""

    entries: int
    visited: set[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class _SkillRead:
    """Связывает байты с открытым каталогом до завершения проверки."""

    payload: bytes
    directory: Handle


class RegistryFilesystem:
    """Предоставляет реестру операции над одним проверенным корнем."""

    __slots__ = ("_attempts", "_directories", "_root")

    def __init__(self, root: Handle) -> None:
        """Сохраняет корневой дескриптор и внутренние бюджеты.

        Args:
            root: проверенный открытый корень.
        """
        self._root = root
        self._directories: set[tuple[int, int]] = set()
        self._attempts = 0

    def list_candidates(self) -> list[str]:
        """Возвращает ограниченные имена кандидатов в стабильном порядке.

        Returns:
            Имена непосредственных записей корня.

        Raises:
            RegistryLimitError: число записей превышает общий предел.
            OSError: перечисление корня завершилось ошибкой ОС.
        """
        names: list[str] = []
        for name in _visible_names(self._root):
            names.append(name)
            _check_root_entry_count(len(names))
        return sorted(names, key=lambda value: (value.casefold(), value))

    @contextmanager
    def read_skill(
        self,
        name: str,
        remaining: int,
    ) -> Iterator[_SkillRead | None]:
        """Читает SKILL.md кандидата и удерживает его каталог открытым.

        Args:
            name: имя непосредственной записи корня.
            remaining: оставшийся суммарный бюджет байтов.

        Yields:
            Байты с контекстом каталога либо отсутствие для не-кандидата.
        """
        directory = self._candidate_directory(name)
        if directory is None:
            yield None
            return
        try:
            yield _SkillRead(self._skill_payload(directory, remaining), directory)
        finally:
            _backend.close(directory)

    def has_additional_files(self, source: _SkillRead) -> bool:
        """Определяет наличие обычного файла в дереве прочитанного скилла.

        Args:
            source: результат чтения из удерживаемого каталога.

        Returns:
            Признак хотя бы одного разрешённого дополнительного файла.

        Raises:
            InvalidSkillError: каталог нечитаем или превышен бюджет обхода.
        """
        state = _WalkState(entries=0, visited={source.directory.identity})
        try:
            return _visit_directory(self._root, source.directory, state, depth=0)
        except OSError:
            return reject("directory_unreadable")

    def _candidate_directory(self, name: str) -> Handle | None:
        handle = self._open_candidate(name)
        try:
            accepted = self._accept_candidate(handle)
        except InvalidSkillError:
            _backend.close(handle)
            raise
        if accepted:
            return handle
        _backend.close(handle)
        return None

    def _open_candidate(self, name: str) -> Handle:
        try:
            return _backend.open_child(self._root, self._root, name)
        except OSError:
            return reject("unsafe_path")

    def _accept_candidate(self, handle: Handle) -> bool:
        _require_containment(handle, self._root.final_path)
        if not handle.is_directory:
            return False
        if handle.identity in self._directories:
            return False
        self._directories.add(handle.identity)
        self._consume_attempt()
        return True

    def _consume_attempt(self) -> None:
        if self._attempts >= MAX_SKILLS:
            reject("registry_limit_exceeded")
        self._attempts += 1

    def _skill_payload(self, directory: Handle, remaining: int) -> bytes:
        try:
            return _read_named_skill(self._root, directory, remaining)
        except OSError as exc:
            return reject(_skill_io_reason(exc))


@contextmanager
def open_registry(expected_root: Path) -> Iterator[RegistryFilesystem]:
    """Открывает и проверяет корень до первой операции перечисления.

    Args:
        expected_root: результат доверенного строгого resolve.

    Yields:
        Семантическая файловая граница одной загрузки.

    Raises:
        InvalidSkillError: тип или конечный путь корня не совпадает с ожиданием.
        OSError: корень не удалось открыть.
    """
    root = _backend.open_path(expected_root)
    try:
        _validate_root(root, expected_root)
        yield RegistryFilesystem(root)
    finally:
        _backend.close(root)


def _validate_root(root: Handle, expected: Path) -> None:
    if not root.is_directory:
        reject("root_not_directory")
    if _normalized_path(root.final_path) != _normalized_path(expected):
        reject("root_unreadable")


def _read_named_skill(root: Handle, directory: Handle, remaining: int) -> bytes:
    handle = _backend.open_child(root, directory, "SKILL.md")
    try:
        return _read_open_skill(handle, root.final_path, remaining)
    finally:
        _backend.close(handle)


def _read_open_skill(handle: Handle, root: Path, remaining: int) -> bytes:
    _require_containment(handle, root)
    if not handle.is_regular:
        return reject("missing_file")
    return _bounded_payload(handle, remaining)


def _bounded_payload(handle: Handle, remaining: int) -> bytes:
    read_size = min(MAX_SKILL_FILE_BYTES, remaining) + 1
    payload = _read_up_to(handle, read_size)
    if len(payload) > MAX_SKILL_FILE_BYTES:
        return reject("file_too_large")
    if len(payload) > remaining:
        return reject("registry_limit_exceeded")
    return payload


def _read_up_to(handle: Handle, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = _backend.read(handle, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _visit_directory(
    root: Handle,
    directory: Handle,
    state: _WalkState,
    depth: int,
) -> bool:
    for name in _visible_names(directory):
        if _inspect_entry(root, directory, name, state, depth):
            return True
    return False


def _inspect_entry(
    root: Handle,
    directory: Handle,
    name: str,
    state: _WalkState,
    depth: int,
) -> bool:
    if depth == 0 and name == "SKILL.md":
        return False
    _consume_entry(state)
    handle = _open_auxiliary(root, directory, name)
    if handle is None:
        return False
    return _inspect_and_close(root, handle, state, depth + 1)


def _open_auxiliary(root: Handle, directory: Handle, name: str) -> Handle | None:
    try:
        return _backend.open_child(root, directory, name)
    except UnsafePathError:
        return None


def _inspect_and_close(
    root: Handle,
    handle: Handle,
    state: _WalkState,
    depth: int,
) -> bool:
    try:
        return _inspect_open_entry(root, handle, state, depth)
    finally:
        _backend.close(handle)


def _inspect_open_entry(
    root: Handle,
    handle: Handle,
    state: _WalkState,
    depth: int,
) -> bool:
    if not _is_within(handle.final_path, root.final_path):
        return False
    if handle.is_directory:
        return _inspect_directory(root, handle, state, depth)
    return handle.is_regular


def _inspect_directory(
    root: Handle,
    directory: Handle,
    state: _WalkState,
    depth: int,
) -> bool:
    if directory.identity in state.visited:
        return False
    _check_depth(depth)
    state.visited.add(directory.identity)
    return _visit_directory(root, directory, state, depth)


def _visible_names(directory: Handle) -> Iterator[str]:
    return filter(_is_visible_name, _backend.iter_entries(directory))


def _require_containment(handle: Handle, root: Path) -> None:
    if not _is_within(handle.final_path, root):
        reject("unsafe_path")


def _skill_io_reason(exc: OSError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "missing_file"
    if isinstance(exc, UnsafePathError):
        return "unsafe_path"
    return "file_unreadable"


def _consume_entry(state: _WalkState) -> None:
    state.entries += 1
    if state.entries > MAX_AUX_ENTRIES:
        reject("directory_limit_exceeded")


def _check_depth(depth: int) -> None:
    if depth > MAX_AUX_DEPTH:
        reject("directory_limit_exceeded")


def _check_root_entry_count(count: int) -> None:
    if count > MAX_ROOT_ENTRIES:
        reject_registry_limit()


def _is_visible_name(name: str) -> bool:
    return name not in {".", ".."}


def _is_within(path: Path, root: Path) -> bool:
    normalized_path = _normalized_path(path)
    normalized_root = _normalized_path(root)
    try:
        common = os.path.commonpath((normalized_path, normalized_root))
    except ValueError:
        return False
    return common == normalized_root


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(path.absolute()))
