"""Собирает classify-запрос без материала и тела скилла."""

from collections.abc import Sequence

from skillhub.classifier._models import SkillMetadata
from skillhub.llm import LlmMessage, LlmRequest

DESCRIPTION_LIMIT = 250

_SYSTEM = (
    "Выбери ровно один режим по намерению и списку описаний. "
    'Верни только JSON {"skill": "<имя>"} или {"skill": null}. '
    "Имя бери только из переданного списка режимов. "
    "Если ни один режим не подходит, верни null. "
    "Материал и тело скилла недоступны и запрещены."
)


def build_request(intent: str, metadata: Sequence[SkillMetadata]) -> LlmRequest:
    """Собирает запрос классификатора без материала.

    Args:
        intent: намерение пользователя.
        metadata: снимок имён и описаний валидных скиллов.

    Returns:
        Типизированный запрос к шлюзу модели.
    """
    return LlmRequest(
        messages=(
            LlmMessage(role="system", content=_SYSTEM),
            LlmMessage(role="user", content=_user_message(intent, metadata)),
        )
    )


def _user_message(intent: str, metadata: Sequence[SkillMetadata]) -> str:
    lines = ["Намерение:", intent, "", "Режимы:"]
    lines.extend(_skill_lines(metadata))
    return "\n".join(lines)


def _skill_lines(metadata: Sequence[SkillMetadata]) -> list[str]:
    return [
        f"- {item.name}: {item.description[:DESCRIPTION_LIMIT]}" for item in metadata
    ]
