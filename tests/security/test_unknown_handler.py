"""Проверяет отказ unknown handler без утечки пути, prompt и ключа."""

from pathlib import Path

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from skillhub.assistant import PromptSkillHandler, SkillHandlerRegistry
from skillhub.llm import FakeLlmGateway, LlmResult, LlmUsage
from tests.security._http import app_client, csrf_token, post_assistant, write_skill
from tests.security._leaks import assert_no_secret_leak, leak_markers

_KEY, _PROMPT, _ = leak_markers()


def test_unknown_handler_rejects_without_path_prompt_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет handler_unavailable и отсутствие секретов для чужого имени."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    root = tmp_path / "skills"
    write_skill(root / "leaked-skill", name="leaked-skill", body=_PROMPT)
    client, gateway = app_client(root, '{"skill": "leaked-skill"}')
    token = csrf_token(client)

    with capture_logs(processors=[merge_contextvars]) as logs:
        response = post_assistant(client, token, "Суммируй", "абзац")

    assert response.status_code == 200
    assert response.json()["outcome"] == "handler_unavailable"
    assert response.json()["text"] is None
    assert response.json()["message"] == "Обработчик выбранного режима недоступен."
    assert _empty_registry().resolve("leaked-skill") is None
    assert len(gateway.requests) == 1
    assert_no_secret_leak(response.text, logs)
    assert str(root.resolve()) not in response.text
    assert str(root.resolve()) not in repr(logs)


def _empty_registry() -> SkillHandlerRegistry:
    """Собирает реестр без extra-обработчиков."""
    return SkillHandlerRegistry(
        PromptSkillHandler(
            FakeLlmGateway(
                result=LlmResult(
                    text="не должен",
                    finish_reason="stop",
                    usage=LlmUsage(
                        prompt_tokens=1,
                        completion_tokens=1,
                        total_tokens=2,
                    ),
                )
            )
        )
    )
