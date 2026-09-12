"""Определяет типизированные отказы шлюза модели."""

from skillhub.core import SkillHubError


class GenerationUnavailableError(SkillHubError):
    """Описывает отсутствие ключа поставщика."""

    code = "generation_unavailable"
    status_code = 503
    public_message = "Генерация недоступна."


class ProviderError(SkillHubError):
    """Описывает отказ поставщика, таймаут или неполный ответ."""

    code = "provider_error"
    status_code = 502
    public_message = "Поставщик модели не выполнил запрос."
