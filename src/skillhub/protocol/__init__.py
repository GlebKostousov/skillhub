"""Предоставляет публичный фасад нормативной модели протокола."""

from skillhub.protocol._constants import (
    GRAMMAR_VERSION,
    H1_PREFIX,
    PLACEHOLDER,
    SECTION_TITLES,
)
from skillhub.protocol._errors import ProtocolParseError
from skillhub.protocol._models import Protocol, ProtocolTask

__all__ = [
    "GRAMMAR_VERSION",
    "H1_PREFIX",
    "PLACEHOLDER",
    "SECTION_TITLES",
    "Protocol",
    "ProtocolParseError",
    "ProtocolTask",
]
