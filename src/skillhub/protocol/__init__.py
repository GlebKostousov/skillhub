"""Предоставляет публичный фасад модели, разбора и воспроизведения протокола."""

from skillhub.protocol._constants import (
    GRAMMAR_VERSION,
    H1_PREFIX,
    PLACEHOLDER,
    SECTION_TITLES,
)
from skillhub.protocol._errors import ProtocolParseError
from skillhub.protocol._models import Protocol, ProtocolTask
from skillhub.protocol._parser import parse
from skillhub.protocol._renderer import render

__all__ = [
    "GRAMMAR_VERSION",
    "H1_PREFIX",
    "PLACEHOLDER",
    "SECTION_TITLES",
    "Protocol",
    "ProtocolParseError",
    "ProtocolTask",
    "parse",
    "render",
]
