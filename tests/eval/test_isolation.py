"""Проверяет, что рантайм skillhub не импортирует пакет eval."""

import ast
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SKILLHUB_ROOT = _REPOSITORY_ROOT / "src" / "skillhub"


def _imported_modules(tree: ast.AST) -> list[str]:
    """Собирает имена модулей из узлов импорта.

    Args:
        tree: синтаксическое дерево исходника.

    Returns:
        Полные имена импортированных модулей.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def _is_eval_import(module: str) -> bool:
    """Проверяет, что имя модуля указывает на пакет eval.

    Args:
        module: полное имя импорта.

    Returns:
        Признак импорта пакета eval.
    """
    return module == "eval" or module.startswith("eval.")


def test_skillhub_sources_do_not_import_eval() -> None:
    """Проверяет AST-страж: skillhub не импортирует eval."""
    violations: list[str] = []
    for source_path in _SKILLHUB_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        relative = source_path.relative_to(_REPOSITORY_ROOT)
        violations.extend(
            f"{relative}:{module}"
            for module in _imported_modules(tree)
            if _is_eval_import(module)
        )

    assert violations == []
