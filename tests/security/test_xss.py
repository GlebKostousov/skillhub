"""Проверяет, что XSS остаётся текстом и не раскрывает путь, prompt и ключ."""

from pathlib import Path

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from tests.security._http import app_client, csrf_token, post_assistant, write_skill
from tests.security._leaks import assert_no_secret_leak, leak_markers

_KEY, _PROMPT, _ = leak_markers()
_XSS = "<script>alert(1)</script><img src=x onerror=alert(2)>"


def test_xss_stays_plain_text_without_path_prompt_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет textContent, экранирование и отсутствие секретов при XSS."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    root = tmp_path / "skills"
    write_skill(
        root / "text-summary",
        name="text-summary",
        caption=_XSS,
        body=_PROMPT,
    )
    client, _gateway = app_client(root, '{"skill": "text-summary"}', _XSS)
    token = csrf_token(client)

    with capture_logs(processors=[merge_contextvars]) as logs:
        page = client.get("/skills")
        response = post_assistant(client, token, "Суммируй", _XSS)
        script = client.get("/static/assistant.js").text

    assert response.status_code == 200
    assert response.json()["text"] == _XSS
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "innerHTML" not in script
    assert "textContent" in script
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert_no_secret_leak(response.text, page.text, logs, script)
    assert str(root.resolve()) not in response.text
    assert str(root.resolve()) not in page.text
    assert str(root.resolve()) not in repr(logs)
