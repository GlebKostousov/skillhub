"""Собирает HTTP-шов окна ассистента."""

from collections.abc import Mapping
from typing import TypedDict

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from skillhub.assistant import (
    Assistant,
    PromptSkillHandler,
    ProtocolSkillHandler,
    SkillHandlerRegistry,
)
from skillhub.classifier import SkillClassifier
from skillhub.llm import LlmGateway
from skillhub.registry import Skill, SkillRegistry
from skillhub.web._assistant_guard import (
    validate_assistant_request,
    validate_csrf_token,
)


class AssistantResponse(TypedDict):
    """Публичный JSON-исход одного обращения к ассистенту."""

    selected_skill: str | None
    caption: str | None
    outcome: str
    text: str | None
    message: str


class _AssistantRoutes:
    """Связывает тонкие HTTP-обработчики с оркестрацией ассистента."""

    __slots__ = ("_assistant", "_csrf_token", "_registry")

    def __init__(
        self,
        registry: SkillRegistry,
        csrf_token: str,
        llm_gateway: LlmGateway,
    ) -> None:
        """Сохраняет реестр, секрет и собранного ассистента.

        Args:
            registry: единственный файловый реестр приложения.
            csrf_token: секрет CSRF текущего экземпляра приложения.
            llm_gateway: шлюз модели для выбора режима и текстового ответа.
        """
        self._registry = registry
        self._csrf_token = csrf_token
        protocol_handler = ProtocolSkillHandler(llm_gateway)
        self._assistant = Assistant(
            SkillClassifier(llm_gateway),
            SkillHandlerRegistry(
                PromptSkillHandler(llm_gateway),
                extra={"meeting-protocol": protocol_handler},
            ),
        )

    async def create_turn(self, request: Request) -> AssistantResponse:
        """Принимает намерение и материал, возвращает типизированный исход.

        Args:
            request: недоверенный JSON-запрос окна ассистента.

        Returns:
            Публичные поля исхода без тела скилла.
        """
        intent, material, stage = await validate_assistant_request(
            request,
            self._csrf_token,
        )
        snapshot = self._registry.capture().snapshot
        if stage == "classify":
            outcome = await run_in_threadpool(
                self._assistant.classify,
                intent,
                snapshot,
            )
        else:
            outcome = await run_in_threadpool(
                self._assistant.run,
                intent,
                material,
                snapshot,
            )
        return {
            "selected_skill": outcome.selected_skill,
            "caption": outcome.caption,
            "outcome": outcome.outcome,
            "text": outcome.text,
            "message": outcome.message,
        }


def create_assistant_router(
    templates: Jinja2Templates,
    *,
    registry: SkillRegistry,
    csrf_token: str,
    llm_gateway: LlmGateway,
) -> APIRouter:
    """Собирает маршрутизатор окна ассистента.

    Args:
        templates: шаблоны серверных страниц, включая главную.
        registry: единственный файловый реестр приложения.
        csrf_token: секрет CSRF из безопасных для URL символов ASCII длиной 1–256.
        llm_gateway: шлюз модели текущего экземпляра приложения.

    Returns:
        Маршрутизатор с `POST /api/assistant`.

    Raises:
        ValueError: настроенный секрет CSRF не соответствует контракту.
    """
    validate_csrf_token(csrf_token)
    _publish_template_globals(templates, registry, csrf_token)
    router = APIRouter()
    routes = _AssistantRoutes(registry, csrf_token, llm_gateway)
    router.add_api_route(
        "/api/assistant",
        routes.create_turn,
        methods=["POST"],
        response_model=AssistantResponse,
    )
    return router


def _publish_template_globals(
    templates: Jinja2Templates,
    registry: SkillRegistry,
    csrf_token: str,
) -> None:
    def assistant_modes() -> tuple[Mapping[str, str], ...]:
        """Возвращает имена и подписи текущего поколения реестра."""
        return _modes_from(registry)

    templates.env.globals["csrf_token"] = csrf_token
    templates.env.globals["assistant_modes"] = assistant_modes


def _modes_from(registry: SkillRegistry) -> tuple[Mapping[str, str], ...]:
    return tuple(
        {"name": skill.name, "caption": skill.caption}
        for skill in _skills_from(registry)
    )


def _skills_from(registry: SkillRegistry) -> tuple[Skill, ...]:
    return registry.capture().search("")
