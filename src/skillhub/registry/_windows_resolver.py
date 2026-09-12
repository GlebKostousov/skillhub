"""Итеративно открывает потомков Windows от удерживаемых дескрипторов."""

from collections import deque
from dataclasses import dataclass

from skillhub.registry._platform_types import Handle, UnsafePathError
from skillhub.registry._resolution import ResolutionBudget
from skillhub.registry._windows_abi import _GENERIC_READ, _CloseHandle
from skillhub.registry._windows_handles import (
    _make_handle,
    _open_relative,
    _probe_relative,
    _reparse_data_from_open_token,
    close,
)
from skillhub.registry._windows_reparse import (
    parse_reparse_target,
    target_components,
)


@dataclass(slots=True)
class _ResolutionState:
    """Хранит одну ограниченную очередь и один промежуточный дескриптор."""

    parent: Handle
    pending: deque[str]
    budget: ResolutionBudget
    owned_parent: Handle | None = None


def open_child(root: Handle, parent: Handle, name: str) -> Handle:
    """Открывает потомка относительно переданного родительского дескриптора.

    Args:
        root: открытый корень разрешённого дерева.
        parent: открытый родительский каталог.
        name: имя непосредственного потомка.

    Returns:
        Метаданные открытого дескриптора Windows.
    """
    return _resolve_components(root, parent, (name,), ResolutionBudget())


def _resolve_components(
    root: Handle,
    parent: Handle,
    components: tuple[str, ...],
    budget: ResolutionBudget,
) -> Handle:
    state = _resolution_state(parent, components, budget)
    try:
        while state.pending:
            result = _visit_component(root, state)
            if result is not None:
                return result
        return _duplicate_parent(state.parent)
    finally:
        _close_owned(state)


def _resolution_state(
    parent: Handle,
    components: tuple[str, ...],
    budget: ResolutionBudget,
) -> _ResolutionState:
    budget.require_pending(len(components))
    return _ResolutionState(parent, deque(components), budget)


def _visit_component(
    root: Handle,
    state: _ResolutionState,
) -> Handle | None:
    name = state.pending.popleft()
    state.budget.visit_component()
    reparse_data = _probe_relative(state.parent, name)
    if reparse_data is not None:
        _follow_reparse(root, state, reparse_data)
        return None
    handle, raced_data = _open_plain_component(state.parent, name)
    if raced_data is not None:
        _follow_reparse(root, state, raced_data)
        return None
    return _accept_plain(state, handle)


def _open_plain_component(
    parent: Handle,
    name: str,
) -> tuple[Handle, bytes | None]:
    token = _open_relative(parent, name, _GENERIC_READ)
    try:
        reparse_data = _reparse_data_from_open_token(token)
    except OSError:
        _CloseHandle(token)
        raise
    if reparse_data is not None:
        _CloseHandle(token)
        return parent, reparse_data
    return _make_handle(token), None


def _follow_reparse(
    root: Handle,
    state: _ResolutionState,
    data: bytes,
) -> None:
    state.budget.follow_link()
    target, relative = parse_reparse_target(data)
    components = target_components(
        target,
        relative=relative,
        parent=state.parent.final_path,
        root=root.final_path,
        remaining=tuple(state.pending),
    )
    state.budget.require_pending(len(components))
    state.pending = deque(components)
    _replace_parent(state, root, owned=False)


def _accept_plain(
    state: _ResolutionState,
    handle: Handle,
) -> Handle | None:
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


def _duplicate_parent(parent: Handle) -> Handle:
    return _make_handle(_open_relative(parent, ".", _GENERIC_READ))
