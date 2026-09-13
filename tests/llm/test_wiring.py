"""Проверяет сборку приложения со швом шлюза модели."""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from skillhub import web
from skillhub.app_factory import create_app
from skillhub.llm import DeepSeekLlmGateway, FakeLlmGateway, LlmResult, LlmUsage
from skillhub.runtime import RuntimeStore
from skillhub.web import create_router
from skillhub.web.usage_routes import LoggingLlmGateway


def test_create_app_does_not_select_fake_gateway() -> None:
    """Проверяет, что production не выбирает тестовый шов сам."""
    app = create_app()

    assert isinstance(app.state.llm_gateway, LoggingLlmGateway)
    assert isinstance(app.state.llm_transport, DeepSeekLlmGateway)
    assert isinstance(app.state.runtime_store, RuntimeStore)
    assert not isinstance(app.state.llm_gateway, FakeLlmGateway)
    assert not isinstance(app.state.llm_gateway, DeepSeekLlmGateway)


def test_create_app_accepts_explicit_fake_gateway() -> None:
    """Проверяет явную подстановку тестового шва в корень сборки."""
    fake = FakeLlmGateway(
        result=LlmResult(
            text="ok",
            finish_reason="stop",
            usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
    )

    app = create_app(llm_gateway=fake)

    assert app.state.llm_gateway is not fake
    assert isinstance(app.state.llm_gateway, LoggingLlmGateway)
    assert app.state.llm_transport is fake


def test_create_app_includes_assistant_router_when_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет необязательное подключение маршрутизатора ассистента."""
    router = APIRouter()

    @router.get("/api/assistant-probe")
    def probe() -> dict[str, str]:
        """Возвращает сигнал наличия маршрута ассистента."""
        return {"status": "wired"}

    def create_assistant_router(*_args: object, **_kwargs: object) -> APIRouter:
        """Возвращает тестовый маршрутизатор ассистента."""
        return router

    monkeypatch.setattr(
        web,
        "create_assistant_router",
        create_assistant_router,
        raising=False,
    )

    response = TestClient(create_app()).get("/api/assistant-probe")

    assert response.status_code == 200
    assert response.json() == {"status": "wired"}


def test_catalog_router_remains_unrelated_to_fake_selection() -> None:
    """Проверяет, что каталожный маршрутизатор не выбирает шлюз."""
    assert "llm" not in create_router.__code__.co_varnames
    assert "llm_gateway" not in create_router.__code__.co_varnames
