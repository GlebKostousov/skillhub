"""Разбирает и нормализует недоверенную цель точки повторного анализа Windows."""

import ntpath
import struct
from pathlib import Path, PureWindowsPath

from skillhub.registry._platform_types import UnsafePathError

_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
_SYMLINK_FLAG_RELATIVE = 0x00000001
_REPARSE_HEADER_BYTES = 8
_MOUNT_POINT_PATH_OFFSET = 16
_SYMLINK_PATH_OFFSET = 20


def parse_reparse_target(data: bytes) -> tuple[str, bool]:
    """Возвращает цель поддерживаемой точки повторного анализа без её открытия.

    Args:
        data: нативный буфер точки повторного анализа.

    Returns:
        Цель и признак относительной символической ссылки.
    """
    tag, data_end = _reparse_header(data)
    parser = {
        _IO_REPARSE_TAG_MOUNT_POINT: _parse_mount_point_target,
        _IO_REPARSE_TAG_SYMLINK: _parse_symlink_target,
    }.get(tag)
    if parser is None:
        raise UnsafePathError from None
    return parser(data, data_end)


def target_components(
    target: str,
    *,
    relative: bool,
    parent: Path,
    root: Path,
    remaining: tuple[str, ...],
) -> tuple[str, ...]:
    """Сводит доверенную внутреннюю цель к компонентам от корня.

    Args:
        target: строка, прочитанная из точки повторного анализа.
        relative: признак относительной формы цели.
        parent: конечный путь удерживаемого родителя.
        root: конечный путь доверенного корня.
        remaining: компоненты после ссылки.

    Returns:
        Компоненты внутренней цели и оставшегося суффикса.
    """
    candidate = _target_path(target, parent, relative=relative)
    root_value = ntpath.normcase(ntpath.abspath(root))
    candidate_value = ntpath.normcase(ntpath.abspath(candidate))
    try:
        common = ntpath.commonpath((root_value, candidate_value))
    except ValueError:
        raise UnsafePathError from None
    if common != root_value:
        raise UnsafePathError from None
    relative_value = ntpath.relpath(candidate_value, root_value)
    return (*_relative_path_parts(relative_value), *remaining)


def _reparse_header(data: bytes) -> tuple[int, int]:
    if len(data) < _REPARSE_HEADER_BYTES:
        raise UnsafePathError from None
    tag, data_length, _reserved = _struct_unpack("<IHH", data, 0)
    data_end = _REPARSE_HEADER_BYTES + data_length
    if data_end > len(data):
        raise UnsafePathError from None
    return tag, data_end


def _parse_mount_point_target(data: bytes, data_end: int) -> tuple[str, bool]:
    if data_end < _MOUNT_POINT_PATH_OFFSET:
        raise UnsafePathError from None
    offset, length, print_offset, print_length = _struct_unpack(
        "<HHHH",
        data,
        8,
    )
    path_length = data_end - _MOUNT_POINT_PATH_OFFSET
    _validate_reparse_name_bounds(offset, length, path_length)
    _validate_reparse_name_bounds(print_offset, print_length, path_length)
    target = _decode_reparse_name(
        data,
        _MOUNT_POINT_PATH_OFFSET,
        offset,
        length,
    )
    return target, False


def _parse_symlink_target(data: bytes, data_end: int) -> tuple[str, bool]:
    if data_end < _SYMLINK_PATH_OFFSET:
        raise UnsafePathError from None
    offset, length, print_offset, print_length, flags = _struct_unpack(
        "<HHHHI",
        data,
        8,
    )
    path_length = data_end - _SYMLINK_PATH_OFFSET
    _validate_reparse_name_bounds(offset, length, path_length)
    _validate_reparse_name_bounds(print_offset, print_length, path_length)
    target = _decode_reparse_name(
        data,
        _SYMLINK_PATH_OFFSET,
        offset,
        length,
    )
    return target, bool(flags & _SYMLINK_FLAG_RELATIVE)


def _decode_reparse_name(
    data: bytes,
    path_offset: int,
    name_offset: int,
    name_length: int,
) -> str:
    start = path_offset + name_offset
    end = start + name_length
    try:
        target = data[start:end].decode("utf-16-le")
    except UnicodeDecodeError:
        raise UnsafePathError from None
    if not target:
        raise UnsafePathError from None
    return target


def _validate_reparse_name_bounds(
    name_offset: int,
    name_length: int,
    path_length: int,
) -> None:
    if name_offset % 2 or name_length % 2:
        raise UnsafePathError from None
    if name_offset + name_length > path_length:
        raise UnsafePathError from None


def _struct_unpack(
    format_string: str,
    data: bytes,
    offset: int,
) -> tuple[int, ...]:
    values = struct.unpack_from(format_string, data, offset)
    return tuple(int(value) for value in values)


def _target_path(target: str, parent: Path, *, relative: bool) -> str:
    if relative:
        return _relative_target_path(target, parent)
    return ntpath.normpath(_normalized_absolute_target(target))


def _relative_target_path(target: str, parent: Path) -> str:
    if ntpath.isabs(target) or _has_device_prefix(target):
        raise UnsafePathError from None
    return ntpath.normpath(ntpath.join(str(parent), target))


def _normalized_absolute_target(target: str) -> str:
    unc_target = _normalized_unc_target(target)
    if unc_target is not None:
        return unc_target
    _reject_device_target(target)
    normalized = _without_nt_namespace(target)
    _require_drive_absolute(normalized)
    return normalized


def _normalized_unc_target(target: str) -> str | None:
    lowered = target.casefold()
    if lowered.startswith("\\??\\unc\\"):
        return "\\\\" + target[8:]
    if target.startswith("\\\\") and not lowered.startswith(("\\\\?\\", "\\\\.\\")):
        return target
    return None


def _reject_device_target(target: str) -> None:
    lowered = target.casefold()
    if lowered.startswith(
        (
            "\\device\\",
            "\\\\.\\",
            "\\??\\globalroot\\",
            "\\\\?\\globalroot\\",
            "\\\\?\\unc\\",
        )
    ):
        raise UnsafePathError from None


def _without_nt_namespace(target: str) -> str:
    if target.casefold().startswith(("\\??\\", "\\\\?\\")):
        return target[4:]
    return target


def _require_drive_absolute(target: str) -> None:
    if not ntpath.isabs(target) or not ntpath.splitdrive(target)[0]:
        raise UnsafePathError from None


def _has_device_prefix(value: str) -> bool:
    lowered = value.casefold()
    return lowered.startswith(("\\??\\", "\\\\?\\", "\\\\.\\", "\\device\\"))


def _relative_path_parts(value: str) -> tuple[str, ...]:
    if value == ".":
        return ()
    return PureWindowsPath(value).parts
