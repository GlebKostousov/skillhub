"""Предоставляет публичный фасад модели, разбора и воспроизведения протокола."""

from skillhub.protocol._clarifications import apply_answers, build_clarifications
from skillhub.protocol._constants import (
    GRAMMAR_VERSION,
    H1_PREFIX,
    MAX_CLARIFICATIONS,
    PLACEHOLDER,
    SECTION_TITLES,
)
from skillhub.protocol._draft import (
    GeneratedDraft,
    ProtocolTextGenerator,
    create_draft,
)
from skillhub.protocol._errors import (
    EmptyClarificationAnswerError,
    ExtraClarificationFieldError,
    InvalidClarificationAnswerError,
    ProtocolGenerationError,
    ProtocolParseError,
    UnknownClarificationIdError,
    UnresolvedClarificationError,
    VerifyMaterialTooLargeError,
)
from skillhub.protocol._parser import parse
from skillhub.protocol._renderer import render
from skillhub.protocol._verify import MAX_VERIFY_CHARS, UnconfirmedClaim, verify
from skillhub.protocol.models import Clarification, Protocol, ProtocolTask

__all__ = [
    "GRAMMAR_VERSION",
    "H1_PREFIX",
    "MAX_CLARIFICATIONS",
    "MAX_VERIFY_CHARS",
    "PLACEHOLDER",
    "SECTION_TITLES",
    "Clarification",
    "EmptyClarificationAnswerError",
    "ExtraClarificationFieldError",
    "GeneratedDraft",
    "InvalidClarificationAnswerError",
    "Protocol",
    "ProtocolGenerationError",
    "ProtocolParseError",
    "ProtocolTask",
    "ProtocolTextGenerator",
    "UnconfirmedClaim",
    "UnknownClarificationIdError",
    "UnresolvedClarificationError",
    "VerifyMaterialTooLargeError",
    "apply_answers",
    "build_clarifications",
    "create_draft",
    "parse",
    "render",
    "verify",
]
