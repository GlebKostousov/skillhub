"""Объявляет используемую часть Win32 и NT ABI."""

import ctypes
from ctypes import wintypes

_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x00000080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_ALL = 0x00000007
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_TYPE_DISK = 0x0001
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ID_BOTH_DIRECTORY_INFO = 10
_FILE_ID_BOTH_DIRECTORY_RESTART_INFO = 11
_ERROR_NO_MORE_FILES = 18
_DIRECTORY_BUFFER_BYTES = 65_536
_FSCTL_GET_REPARSE_POINT = 0x000900A8
_MAX_REPARSE_BYTES = 16_384
_FILE_OPEN = 1
_FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_OBJ_CASE_INSENSITIVE = 0x00000040

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_CreateFileW.restype = wintypes.HANDLE

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL

_GetFileType = _kernel32.GetFileType
_GetFileType.argtypes = [wintypes.HANDLE]
_GetFileType.restype = wintypes.DWORD

_GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
_GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
_GetFinalPathNameByHandleW.restype = wintypes.DWORD

_GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
_GetFileInformationByHandle.restype = wintypes.BOOL

_GetFileInformationByHandleEx = _kernel32.GetFileInformationByHandleEx
_GetFileInformationByHandleEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
]
_GetFileInformationByHandleEx.restype = wintypes.BOOL

_ReadFile = _kernel32.ReadFile
_ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_ReadFile.restype = wintypes.BOOL

_DeviceIoControl = _kernel32.DeviceIoControl
_DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_DeviceIoControl.restype = wintypes.BOOL

_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class _ByHandleFileInformation(ctypes.Structure):
    """Повторяет BY_HANDLE_FILE_INFORMATION из Win32 API."""

    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _DirectoryInfoHeader(ctypes.Structure):
    """Повторяет заголовок FILE_ID_BOTH_DIR_INFO из Win32 API."""

    _fields_ = [
        ("next_entry_offset", wintypes.DWORD),
        ("file_index", wintypes.DWORD),
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("allocation_size", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
        ("file_name_length", wintypes.DWORD),
        ("extended_attributes_size", wintypes.DWORD),
        ("short_name_length", ctypes.c_byte),
        ("alignment_padding", ctypes.c_byte),
        ("short_name", wintypes.WCHAR * 12),
        ("file_id", ctypes.c_longlong),
    ]


class _UnicodeString(ctypes.Structure):
    """Повторяет UNICODE_STRING для относительного открытия."""

    _fields_ = [
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", wintypes.LPWSTR),
    ]


class _ObjectAttributes(ctypes.Structure):
    """Повторяет OBJECT_ATTRIBUTES с корневым дескриптором."""

    _fields_ = [
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(_UnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", wintypes.LPVOID),
        ("security_quality_of_service", wintypes.LPVOID),
    ]


class _IoStatusBlock(ctypes.Structure):
    """Повторяет IO_STATUS_BLOCK для синхронного открытия."""

    _fields_ = [
        ("status", wintypes.LPVOID),
        ("information", ctypes.c_size_t),
    ]


_GetFileInformationByHandle.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_ByHandleFileInformation),
]

_NtCreateFile = _ntdll.NtCreateFile
_NtCreateFile.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.ULONG,
    ctypes.POINTER(_ObjectAttributes),
    ctypes.POINTER(_IoStatusBlock),
    ctypes.POINTER(ctypes.c_longlong),
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.LPVOID,
    wintypes.ULONG,
]
_NtCreateFile.restype = ctypes.c_long

_RtlNtStatusToDosError = _ntdll.RtlNtStatusToDosError
_RtlNtStatusToDosError.argtypes = [ctypes.c_long]
_RtlNtStatusToDosError.restype = wintypes.ULONG
