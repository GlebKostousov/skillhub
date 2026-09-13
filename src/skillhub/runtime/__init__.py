"""Предоставляет снимок и атомарную запись runtime-настроек.

Пакет видит только skillhub.core; лимиты моделей задаёт вызывающий код.
"""

from skillhub.runtime._errors import InvalidOverlayError, OverlayUnavailableError
from skillhub.runtime._models import OverlayField, OverlayValues, RuntimeSnapshot
from skillhub.runtime._store import RuntimeStore

__all__ = [
    "InvalidOverlayError",
    "OverlayField",
    "OverlayUnavailableError",
    "OverlayValues",
    "RuntimeSnapshot",
    "RuntimeStore",
]
