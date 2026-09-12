"""Предоставляет публичный фасад модели, разбора и воспроизведения протокола."""

from skillhub.protocol._constants import (
    GRAMMAR_VERSION,
    H1_PREFIX,
    PLACEHOLDER,
    SECTION_TITLES,
)
from skillhub.protocol._draft import (
    GeneratedDraft,
    ProtocolTextGenerator,
    create_draft,
)
from skillhub.protocol._errors import ProtocolGenerationError, ProtocolParseError
from skillhub.protocol._parser import parse
from skillhub.protocol._renderer import render
from skillhub.protocol.models import Protocol, ProtocolTask

__all__ = [
    "GRAMMAR_VERSION",
    "H1_PREFIX",
    "PLACEHOLDER",
    "SECTION_TITLES",
    "GeneratedDraft",
    "Protocol",
    "ProtocolGenerationError",
    "ProtocolParseError",
    "ProtocolTask",
    "ProtocolTextGenerator",
    "create_draft",
    "parse",
    "render",
]
