"""Проверяет, что CI повторяет локальный gate и добавляет audit."""

import re
from pathlib import Path

from tests.delivery._files import joined_workflows, repository_root

_GATE_BLOCK = re.compile(
    r"```console\r?\n"
    r"(uv run --no-sync ruff check \.)\r?\n"
    r"(uv run --no-sync ruff format --check \.)\r?\n"
    r"(uv run --no-sync mypy src tests)\r?\n"
    r"(uv run --no-sync lint-imports)\r?\n"
    r"(uv run --no-sync pytest)\r?\n"
    r"```"
)
_SECRET_SCAN_MARKERS = (
    "trufflehog",
    "gitleaks",
    "detect-secrets",
    "secretlint",
)


def _documented_local_gate() -> tuple[str, ...]:
    """Извлекает локальный quality-gate из README.

    Returns:
        Пять документированных команд проверки.
    """
    readme = (repository_root() / "README.md").read_text(encoding="utf-8")
    match = _GATE_BLOCK.search(readme)
    assert match is not None
    return match.groups()


def test_workflow_repeats_documented_local_gate() -> None:
    """Проверяет, что workflow содержит команды локального gate."""
    workflow = joined_workflows()
    missing = [
        command for command in _documented_local_gate() if command not in workflow
    ]
    assert missing == []


def test_workflow_fails_when_branch_coverage_is_below_official_threshold() -> None:
    """Проверяет явный порог покрытия 85 % в CI."""
    workflow = joined_workflows()
    assert "pytest" in workflow
    assert "--cov-fail-under=85" in workflow
    assert "--no-cov" not in workflow


def test_workflow_syncs_the_pinned_lockfile() -> None:
    """Проверяет установку зависимостей из зафиксированного uv.lock."""
    assert "uv sync --frozen" in joined_workflows()


def test_workflow_runs_dependency_audit() -> None:
    """Проверяет наличие pip-audit в quality-gate."""
    assert "pip-audit" in joined_workflows()


def test_workflow_runs_secret_scan() -> None:
    """Проверяет наличие сканера секретов в quality-gate."""
    workflow = joined_workflows().lower()
    assert any(marker in workflow for marker in _SECRET_SCAN_MARKERS)


def test_workflows_live_only_under_github_directory() -> None:
    """Проверяет, что контракт CI лежит в заявленном каталоге."""
    directory = Path(repository_root()) / ".github" / "workflows"
    assert directory.is_dir()
    assert any(path.suffix in {".yml", ".yaml"} for path in directory.iterdir())
