"""Предоставляет публичный тестовый шов шлюза модели."""

from skillhub.core import SkillHubError
from skillhub.llm._errors import GenerationUnavailableError
from skillhub.llm._models import LlmGateway, LlmRequest, LlmResult


class FakeLlmGateway(LlmGateway):
    """Возвращает заранее заданный результат или типизированный отказ."""

    def __init__(
        self,
        *,
        result: LlmResult | None = None,
        error: SkillHubError | None = None,
    ) -> None:
        """Сохраняет фиксированный исход тестового вызова.

        Args:
            result: успешный ответ, если отказ не задан.
            error: типизированный отказ шлюза вместо успешного ответа.
        """
        self._result = result
        self._error = error
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResult:
        """Возвращает заранее заданный исход без обращения к сети.

        Args:
            request: типизированный запрос вызывающей стороны.

        Returns:
            Сохранённый успешный результат.

        Raises:
            GenerationUnavailableError: если результат не задан.
            SkillHubError: заранее заданный отказ.
        """
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise GenerationUnavailableError
        return self._result
