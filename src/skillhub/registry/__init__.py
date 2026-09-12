"""Предоставляет публичный фасад файлового реестра скиллов.

Единственный источник текущего каталога — реестр и его поколение.
"""

from skillhub.registry._limits import MAX_SKILL_FILE_BYTES
from skillhub.registry._models import LoadIssue, LoadReport, Skill
from skillhub.registry._registry import RegistryCapture, SkillRegistry

__all__ = [
    "MAX_SKILL_FILE_BYTES",
    "LoadIssue",
    "LoadReport",
    "RegistryCapture",
    "Skill",
    "SkillRegistry",
]
