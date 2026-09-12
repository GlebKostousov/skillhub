"""Выполняет Win32 I/O только через удерживаемые файловые дескрипторы."""

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import NoReturn

from skillhub.registry._platform_types import Handle, UnsafePathError
from skillhub.registry._windows_abi import (
    _FILE_ATTRIBUTE_DIRECTORY,
    _FILE_ATTRIBUTE_REPARSE_POINT,
    _FILE_FLAG_BACKUP_SEMANTICS,
    _FILE_FLAG_OPEN_REPARSE_POINT,
    _FILE_OPEN,
    _FILE_OPEN_FOR_BACKUP_INTENT,
    _FILE_OPEN_REPARSE_POINT,
    _FILE_READ_ATTRIBUTES,
    _FILE_SHARE_ALL,
    _FILE_SYNCHRONOUS_IO_NONALERT,
    _FILE_TYPE_DISK,
    _FSCTL_GET_REPARSE_POINT,
    _GENERIC_READ,
    _INVALID_HANDLE_VALUE,
    _MAX_REPARSE_BYTES,
    _OBJ_CASE_INSENSITIVE,
    _OPEN_EXISTING,
    _SYNCHRONIZE,
    _ByHandleFileInformation,
    _CloseHandle,
    _CreateFileW,
    _DeviceIoControl,
    _GetFileInformationByHandle,
    _GetFileType,
    _GetFinalPathNameByHandleW,
    _IoStatusBlock,
    _NtCreateFile,
    _ObjectAttributes,
    _ReadFile,
    _RtlNtStatusToDosError,
    _UnicodeString,
)


def open_path(path: Path) -> Handle:
    """Открывает корневой путь без следования конечной точке повторного анализа.

    Args:
        path: путь открываемого объекта.

    Returns:
        Метаданные открытого дескриптора Windows.
    """
    extended_path = _extended_path(path)
    _reject_root_reparse(extended_path)
    return _open_plain_root(extended_path)


def read(handle: Handle, size: int) -> bytes:
    """Читает ограниченный объём с того же дескриптора Windows.

    Args:
        handle: открытый обычный файл.
        size: максимальное число байтов.

    Returns:
        Прочитанные байты.
    """
    buffer = ctypes.create_string_buffer(size)
    bytes_read = wintypes.DWORD()
    if not _ReadFile(handle.token, buffer, size, ctypes.byref(bytes_read), None):
        _raise_last_error()
    return buffer.raw[: bytes_read.value]


def close(handle: Handle) -> None:
    """Закрывает дескриптор Windows без раскрытия ошибки очистки.

    Args:
        handle: закрываемый объект.
    """
    _CloseHandle(handle.token)


def _reject_root_reparse(path: str) -> None:
    token = _open_probe(path)
    try:
        _assert_not_reparse(token)
    finally:
        _CloseHandle(token)


def _open_plain_root(path: str) -> Handle:
    token = _open_content(path)
    try:
        _assert_not_reparse(token)
    except OSError:
        _CloseHandle(token)
        raise
    return _make_handle(token)


def _open_probe(path: str) -> int:
    return _open_raw(path, _FILE_READ_ATTRIBUTES)


def _open_content(path: str) -> int:
    return _open_raw(path, _GENERIC_READ)


def _open_raw(path: str, access: int) -> int:
    raw_handle = _CreateFileW(
        path,
        access,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if raw_handle == _INVALID_HANDLE_VALUE:
        _raise_last_error()
    return int(raw_handle)


def _probe_relative(parent: Handle, name: str) -> bytes | None:
    token = _open_relative(parent, name, _FILE_READ_ATTRIBUTES)
    try:
        return _reparse_data_from_open_token(token)
    finally:
        _CloseHandle(token)


def _reparse_data_from_open_token(token: int) -> bytes | None:
    information = _file_information(token)
    if not information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    return _read_reparse_data(token)


def _open_relative(parent: Handle, name: str, access: int) -> int:
    name_buffer = ctypes.create_unicode_buffer(name)
    byte_length = len(name.encode("utf-16-le"))
    object_name = _UnicodeString(
        length=byte_length,
        maximum_length=byte_length + ctypes.sizeof(wintypes.WCHAR),
        buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = _ObjectAttributes(
        length=ctypes.sizeof(_ObjectAttributes),
        root_directory=parent.token,
        object_name=ctypes.pointer(object_name),
        attributes=_OBJ_CASE_INSENSITIVE,
        security_descriptor=None,
        security_quality_of_service=None,
    )
    result = wintypes.HANDLE()
    io_status = _IoStatusBlock()
    status = _NtCreateFile(
        ctypes.byref(result),
        access | _SYNCHRONIZE,
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        0,
        _FILE_SHARE_ALL,
        _FILE_OPEN,
        _FILE_OPEN_REPARSE_POINT
        | _FILE_OPEN_FOR_BACKUP_INTENT
        | _FILE_SYNCHRONOUS_IO_NONALERT,
        None,
        0,
    )
    if status < 0:
        _raise_ntstatus(status)
    if result.value is None:
        raise OSError from None
    return result.value


def _make_handle(token: int) -> Handle:
    try:
        information = _file_information(token)
        final_path = _final_path(token)
    except OSError:
        _CloseHandle(token)
        raise
    is_directory = bool(information.attributes & _FILE_ATTRIBUTE_DIRECTORY)
    file_index = information.file_index_high << 32 | information.file_index_low
    return Handle(
        token=token,
        final_path=final_path,
        identity=(information.volume_serial_number, file_index),
        is_directory=is_directory,
        is_regular=_GetFileType(token) == _FILE_TYPE_DISK and not is_directory,
    )


def _file_information(token: int) -> _ByHandleFileInformation:
    information = _ByHandleFileInformation()
    if not _GetFileInformationByHandle(token, ctypes.byref(information)):
        _raise_last_error()
    return information


def _assert_not_reparse(token: int) -> None:
    information = _file_information(token)
    if information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafePathError from None


def _read_reparse_data(token: int) -> bytes:
    buffer = ctypes.create_string_buffer(_MAX_REPARSE_BYTES)
    bytes_returned = wintypes.DWORD()
    if not _DeviceIoControl(
        token,
        _FSCTL_GET_REPARSE_POINT,
        None,
        0,
        buffer,
        len(buffer),
        ctypes.byref(bytes_returned),
        None,
    ):
        _raise_last_error()
    return buffer.raw[: bytes_returned.value]


def _final_path(token: int) -> Path:
    required = _GetFinalPathNameByHandleW(token, None, 0, 0)
    if required == 0:
        _raise_last_error()
    buffer = ctypes.create_unicode_buffer(required + 1)
    copied = _GetFinalPathNameByHandleW(token, buffer, len(buffer), 0)
    if copied == 0 or copied >= len(buffer):
        _raise_last_error()
    return _normalized_final_path(buffer.value)


def _normalized_final_path(value: str) -> Path:
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return Path(value)


def _extended_path(path: Path) -> str:
    value = str(path.absolute())
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _raise_last_error() -> NoReturn:
    raise ctypes.WinError(ctypes.get_last_error()) from None


def _raise_ntstatus(status: int) -> NoReturn:
    error_code = _RtlNtStatusToDosError(status)
    raise ctypes.WinError(error_code) from None
