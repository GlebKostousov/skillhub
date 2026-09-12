"""Проверяет отказ CSRF и same-origin без утечки пути, prompt и ключа."""

from pathlib import Path

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from tests.security._http import app_client, csrf_token, write_skill
from tests.security._leaks import assert_no_secret_leak, leak_markers

_KEY, _PROMPT, _ = leak_markers()


def test_csrf_or_foreign_origin_rejects_without_path_prompt_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет 403 для чужого Origin и отсутствие секретов в отказе."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    root = tmp_path / "skills"
    write_skill(root / "text-summary", name="text-summary", body=_PROMPT)
    client, gateway = app_client(root, '{"skill": "text-summary"}', "не должен")
    token = csrf_token(client)

    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.post(
            "/api/assistant",
            json={
                "intent": "Суммируй",
                "material": _PROMPT,
                "csrf_token": token,
            },
            headers={
                "origin": "https://attacker.example",
                "x-csrf-token": token,
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "assistant_forbidden",
            "message": "Запрос ассистента запрещён.",
        }
    }
    assert gateway.requests == []
    assert token not in response.text
    assert token not in repr(logs)
    assert_no_secret_leak(response.text, logs)
    assert str(root.resolve()) not in response.text
    assert str(root.resolve()) not in repr(logs)
