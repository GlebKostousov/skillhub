"""Проверяет отказ path traversal без утечки пути, prompt и ключа."""

from pathlib import Path
from typing import cast

import pytest
from structlog.testing import capture_logs

import skillhub.registry._filesystem as filesystem
from skillhub.core import configure_logging
from skillhub.registry import SkillRegistry
from skillhub.registry._platform_types import Handle, PlatformAdapter
from tests.security._http import write_skill
from tests.security._leaks import assert_no_secret_leak, leak_markers

_KEY, _PROMPT, _ = leak_markers()


def test_external_escape_rejects_without_path_prompt_or_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет unsafe_path и отсутствие секретов при внешней цели."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", _KEY)
    configure_logging("test")
    root = tmp_path / "skills"
    write_skill(root / "redirected", name="redirected", body="внутреннее тело")
    external = tmp_path / "outside.md"
    external.write_text(_PROMPT, encoding="utf-8")
    _redirect_skill_file(monkeypatch, external)

    with capture_logs() as logs:
        report = SkillRegistry(root).load()

    rendered = capsys.readouterr().out
    observed = f"{report!r}{logs!r}{rendered}"
    assert report.skills == ()
    assert report.issues[0].reason == "unsafe_path"
    assert not Path(report.issues[0].path).is_absolute()
    assert_no_secret_leak(observed)
    assert str(external.resolve()) not in observed
    assert str(tmp_path.resolve()) not in observed


def _redirect_skill_file(monkeypatch: pytest.MonkeyPatch, external: Path) -> None:
    """Подменяет открытие SKILL.md внешней целью.

    Args:
        monkeypatch: подмена платформенного адаптера.
        external: файл за корнем реестра.
    """
    backend = cast("PlatformAdapter", vars(filesystem)["_backend"])
    original_open_child = backend.open_child

    def redirect_skill_file(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        """Подменяет только открытый внешний SKILL.md."""
        if name == "SKILL.md":
            return backend.open_path(external)
        return original_open_child(root_handle, parent, name)

    monkeypatch.setattr(backend, "open_child", redirect_skill_file)
