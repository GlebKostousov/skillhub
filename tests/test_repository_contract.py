"""Проверяет воспроизводимость репозитория и границу core."""

import ast
import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GIT = shutil.which("git")


def _run_git(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Выполняет Git-команду в тестируемом репозитории.

    Args:
        arguments: аргументы Git без имени исполняемого файла.

    Returns:
        Завершённый процесс с захваченным текстовым выводом.
    """
    if _GIT is None:
        pytest.fail("Git не найден в PATH.")
    return subprocess.run(  # noqa: S603
        [_GIT, *arguments],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "generated_path",
    [
        ".env",
        ".env.local",
        "local.key",
        "local.pem",
        ".venv/state",
        ".pytest_cache/state",
        ".mypy_cache/state",
        ".ruff_cache/state",
        ".hypothesis/state",
        ".import_linter_cache/state",
        "runtime-overlay.json",
        "build/artifact",
        "dist/artifact",
        "skillhub.egg-info/state",
        ".idea/state",
        ".vscode/state",
        ".DS_Store",
        "Thumbs.db",
        "Desktop.ini",
        ".worktrees/state",
        ".grok-orch-wt/state",
        ".superpowers/state",
        ".serena/project.yml",
        "skillhub.log",
        "scratch.tmp",
    ],
)
def test_generated_and_sensitive_paths_are_ignored(generated_path: str) -> None:
    """Проверяет исключение секретов и локальных артефактов."""
    result = _run_git("check-ignore", "--quiet", "--", generated_path)

    assert result.returncode == 0, generated_path


def test_environment_example_remains_trackable() -> None:
    """Проверяет возможность хранить безопасный пример окружения."""
    result = _run_git("check-ignore", "--quiet", "--", ".env.example")

    assert result.returncode == 1


def test_attributes_define_deterministic_text_and_binary_eol() -> None:
    """Проверяет однозначные EOL-атрибуты публичной Git-командой."""
    result = _run_git(
        "check-attr",
        "text",
        "eol",
        "--",
        "README.md",
        "script.cmd",
        "image.png",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "README.md: text: auto",
        "README.md: eol: lf",
        "script.cmd: text: set",
        "script.cmd: eol: crlf",
        "image.png: text: unset",
        "image.png: eol: unspecified",
    ]


def test_all_tracked_text_is_normalized_in_the_index() -> None:
    """Проверяет LF в индексе для всех отслеживаемых текстовых файлов."""
    result = _run_git("ls-files", "--eol")

    assert result.returncode == 0, result.stderr
    text_entries = [
        line for line in result.stdout.splitlines() if "attr/-text" not in line
    ]
    non_lf_entries = [line for line in text_entries if not line.startswith("i/lf")]
    assert text_entries
    assert non_lf_entries == []


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("core.autocrlf", "false"),
        ("core.safecrlf", "true"),
        ("fetch.prune", "true"),
        ("pull.ff", "only"),
        ("push.autosetupremote", "true"),
    ],
)
def test_required_git_setting_is_repository_local(
    setting: str,
    expected: str,
) -> None:
    """Проверяет локальный источник обязательной Git-настройки."""
    local_result = _run_git("config", "--local", "--get", setting)
    effective_result = _run_git("config", "--get", setting)

    assert local_result.returncode == 0, local_result.stderr
    assert local_result.stdout.strip() == expected
    assert effective_result.returncode == 0, effective_result.stderr
    assert effective_result.stdout.strip() == expected


def test_core_imports_only_its_own_skillhub_package() -> None:
    """Проверяет независимость core от web и будущих прикладных пакетов."""
    violations: list[str] = []
    core_root = _REPOSITORY_ROOT / "src" / "skillhub" / "core"

    for source_path in core_root.rglob("*.py"):
        syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_modules = [node.module or ""]
            else:
                continue
            violations.extend(
                f"{source_path.relative_to(_REPOSITORY_ROOT)}:{node.lineno}:{module}"
                for module in imported_modules
                if module == "skillhub"
                or (
                    module.startswith("skillhub.")
                    and not module.startswith("skillhub.core")
                )
            )

    assert violations == []


def test_entrypoints_only_delegate_to_composition_and_server_boundaries() -> None:
    """Проверяет отсутствие повторной сборки в точках входа процесса."""

    def skillhub_imports(relative_path: str) -> list[str]:
        """Собирает импорты SkillHub из указанной точки входа.

        Args:
            relative_path: путь Python-модуля относительно репозитория.

        Returns:
            Отсортированные полные имена импортированных модулей.
        """
        source_path = _REPOSITORY_ROOT / relative_path
        syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        return sorted(
            node.module or ""
            for node in ast.walk(syntax_tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("skillhub.")
        )

    assert skillhub_imports("src/skillhub/main.py") == ["skillhub._server"]
    assert skillhub_imports("src/skillhub/__main__.py") == ["skillhub._server"]


def test_composition_root_uses_public_web_facade() -> None:
    """Фиксирует отсутствие импорта приватной раскладки web из корня сборки."""
    source_path = _REPOSITORY_ROOT / "src" / "skillhub" / "app_factory.py"
    syntax_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    web = importlib.import_module("skillhub.web")

    assert "skillhub.web._host_guard" not in imported_modules
    assert "StrictHostMiddleware" in web.__all__
    assert web.StrictHostMiddleware.__module__ == "skillhub.web._host_guard"
