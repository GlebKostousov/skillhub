"""Перечисляет каталоги Windows через удерживаемый дескриптор."""

import ctypes
from collections.abc import Iterator

from skillhub.registry._platform_types import Handle
from skillhub.registry._windows_abi import (
    _DIRECTORY_BUFFER_BYTES,
    _ERROR_NO_MORE_FILES,
    _FILE_ID_BOTH_DIRECTORY_INFO,
    _FILE_ID_BOTH_DIRECTORY_RESTART_INFO,
    _DirectoryInfoHeader,
    _GetFileInformationByHandleEx,
)
from skillhub.registry._windows_handles import _raise_last_error


def iter_entries(directory: Handle) -> Iterator[str]:
    """Перечисляет каталог через тот же дескриптор Windows.

    Args:
        directory: открытый каталог.

    Yields:
        Имена непосредственных записей.
    """
    buffer = ctypes.create_string_buffer(_DIRECTORY_BUFFER_BYTES)
    information_class = _FILE_ID_BOTH_DIRECTORY_RESTART_INFO
    while _fill_directory_buffer(directory, information_class, buffer):
        yield from _directory_names(buffer)
        information_class = _FILE_ID_BOTH_DIRECTORY_INFO


def _fill_directory_buffer(
    directory: Handle,
    information_class: int,
    buffer: ctypes.Array[ctypes.c_char],
) -> bool:
    if _GetFileInformationByHandleEx(
        directory.token,
        information_class,
        buffer,
        len(buffer),
    ):
        return True
    if ctypes.get_last_error() == _ERROR_NO_MORE_FILES:
        return False
    return _raise_last_error()


def _directory_names(
    buffer: ctypes.Array[ctypes.c_char],
) -> Iterator[str]:
    offset = 0
    while True:
        information = _DirectoryInfoHeader.from_buffer(buffer, offset)
        name_address = (
            ctypes.addressof(buffer) + offset + ctypes.sizeof(_DirectoryInfoHeader)
        )
        raw_name = ctypes.string_at(name_address, information.file_name_length)
        yield raw_name.decode("utf-16-le")
        if information.next_entry_offset == 0:
            return
        offset += information.next_entry_offset
