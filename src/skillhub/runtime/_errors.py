"""Определяет типизированные отказы снимка и записи runtime-настроек."""

from skillhub.core import SkillHubError


class InvalidOverlayError(SkillHubError):
    """Описывает отказ принять схему или значения наложения."""

    code = "invalid_overlay"
    status_code = 422
    public_message = "Наложение настроек отклонено."


class OverlayUnavailableError(SkillHubError):
    """Описывает отказ атомарно записать файл наложения."""

    code = "overlay_unavailable"
    status_code = 409
    public_message = "Наложение настроек недоступно."
