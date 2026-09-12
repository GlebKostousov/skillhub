"""Задаёт общий контракт низкоуровневого файлового адаптера."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class UnsafePathError(OSError):
    """Обозначает путь, отклонённый до следования внешней ссылке."""


@dataclass(frozen=True, slots=True)
class Handle:
    """Описывает один фактически открытый файловый объект.

    Attributes:
        token: нативный файловый дескриптор.
        final_path: конечный путь открытого объекта.
        identity: идентификатор тома и файла.
        is_directory: признак открытого каталога.
        is_regular: признак обычного дискового файла.
    """

    token: int
    final_path: Path
    identity: tuple[int, int]
    is_directory: bool
    is_regular: bool


class PlatformAdapter(Protocol):
    """Скрывает различия файловых дескрипторов POSIX и Windows."""

    def open_path(self, path: Path) -> Handle:
        """Открывает путь без чтения содержимого.

        Args:
            path: путь открываемого объекта.

        Returns:
            Метаданные открытого объекта.
        """
        ...

    def open_child(self, root: Handle, parent: Handle, name: str) -> Handle:
        """Открывает именованного потомка каталога.

        Args:
            root: открытый корень разрешённого дерева.
            parent: открытый родительский каталог.
            name: имя непосредственного потомка.

        Returns:
            Метаданные открытого объекта.
        """
        ...

    def iter_entries(self, directory: Handle) -> Iterator[str]:
        """Перечисляет имена через открытый каталог.

        Args:
            directory: открытый каталог.

        Yields:
            Имена непосредственных записей.
        """
        ...

    def read(self, handle: Handle, size: int) -> bytes:
        """Читает ограниченный объём из открытого объекта.

        Args:
            handle: открытый обычный файл.
            size: максимальное число байтов.

        Returns:
            Прочитанные байты.
        """
        ...

    def close(self, handle: Handle) -> None:
        """Закрывает открытый объект.

        Args:
            handle: закрываемый объект.
        """
        ...
