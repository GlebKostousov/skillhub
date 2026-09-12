"""Читает контрактные файлы поставки без сборки образа."""

from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_SUFFIXES = (".yml", ".yaml")


def repository_root() -> Path:
    """Возвращает корень проверяемого репозитория."""
    return _REPOSITORY_ROOT


def read_text(relative_path: str) -> str:
    """Читает текстовый файл поставки в UTF-8.

    Args:
        relative_path: путь относительно корня репозитория.

    Returns:
        Содержимое файла.
    """
    path = _REPOSITORY_ROOT / relative_path
    assert path.is_file(), relative_path
    return path.read_text(encoding="utf-8")


def workflow_texts() -> tuple[str, ...]:
    """Собирает тексты всех GitHub workflow.

    Returns:
        Содержимое файлов `.github/workflows`.
    """
    directory = _REPOSITORY_ROOT / ".github" / "workflows"
    assert directory.is_dir(), ".github/workflows"
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in _WORKFLOW_SUFFIXES
    )
    assert files, ".github/workflows"
    return tuple(path.read_text(encoding="utf-8") for path in files)


def joined_workflows() -> str:
    """Склеивает тексты workflow для проверки контракта.

    Returns:
        Объединённый текст всех workflow.
    """
    return "\n".join(workflow_texts())


def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """Разбирает инструкции Dockerfile с продолжением строк.

    Args:
        text: исходный текст Dockerfile.

    Returns:
        Пары «инструкция — аргумент» без комментариев.
    """
    instructions: list[tuple[str, str]] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.split("#", 1)[0].rstrip()
        if not stripped:
            continue
        line = f"{pending} {stripped}".strip() if pending else stripped
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        pending = ""
        name, _, argument = line.partition(" ")
        instructions.append((name.upper(), argument))
    return instructions


def final_stage(instructions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Возвращает инструкции последнего stage.

    Args:
        instructions: разобранный Dockerfile.

    Returns:
        Хвост после последнего `FROM`.
    """
    last_from = max(
        index for index, (name, _) in enumerate(instructions) if name == "FROM"
    )
    return instructions[last_from:]


def instruction_arguments(
    instructions: list[tuple[str, str]],
    name: str,
) -> list[str]:
    """Собирает аргументы указанной инструкции.

    Args:
        instructions: разобранный фрагмент Dockerfile.
        name: имя инструкции.

    Returns:
        Список аргументов в порядке появления.
    """
    return [argument for instruction, argument in instructions if instruction == name]
