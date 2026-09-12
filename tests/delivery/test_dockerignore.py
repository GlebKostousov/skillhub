"""Проверяет, что контекст сборки не тащит git, ключ и локальное окружение."""

from tests.delivery._files import read_text

_REQUIRED_EXCLUDES = (".git", ".env", ".venv")


def _ignore_entries() -> set[str]:
    """Собирает непустые правила .dockerignore без комментариев.

    Returns:
        Множество путей и шаблонов исключения.
    """
    entries: set[str] = set()
    for raw_line in read_text(".dockerignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def test_dockerignore_keeps_git_env_and_venv_out_of_build_context() -> None:
    """Проверяет обязательные исключения контекста сборки."""
    entries = _ignore_entries()
    missing = [pattern for pattern in _REQUIRED_EXCLUDES if pattern not in entries]
    assert missing == []


def test_dockerignore_does_not_mention_provider_key() -> None:
    """Проверяет, что файл игнора не задаёт ключ провайдера."""
    assert "DEEPSEEK_API_KEY" not in read_text(".dockerignore")
