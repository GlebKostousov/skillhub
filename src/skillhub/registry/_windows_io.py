"""Собирает приватный адаптер файловых операций Windows."""

from skillhub.registry._windows_directory import iter_entries
from skillhub.registry._windows_handles import (
    close,
    open_path,
    read,
)
from skillhub.registry._windows_resolver import open_child

__all__ = ["close", "iter_entries", "open_child", "open_path", "read"]
