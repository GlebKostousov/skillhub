"""Проверяет текстовый контракт multi-stage образа без обязательной сборки."""

from pathlib import PurePosixPath

from tests.delivery._files import (
    dockerfile_instructions,
    final_stage,
    instruction_arguments,
    read_text,
)

_CONTAINER_BIND = "0.0.0.0"  # noqa: S104
_APP_FACTORY = PurePosixPath("skillhub/app_factory.py")
_TARIFFS = PurePosixPath("config/model-tariffs.yaml")
_EDITABLE_APP_FACTORY = PurePosixPath("/app/src") / _APP_FACTORY
_WHEEL_APP_FACTORY = (
    PurePosixPath("/app/.venv/lib/python3.13/site-packages") / _APP_FACTORY
)


def _dockerfile() -> str:
    """Возвращает текст корневого Dockerfile."""
    return read_text("Dockerfile")


def _instructions() -> list[tuple[str, str]]:
    """Разбирает инструкции корневого Dockerfile."""
    return dockerfile_instructions(_dockerfile())


def _project_sync_commands() -> list[str]:
    """Собирает команды установки проекта в образе."""
    return [
        argument
        for name, argument in _instructions()
        if name == "RUN"
        and "uv sync" in argument
        and "--no-install-project" not in argument
    ]


def _runtime_copy_destinations() -> list[PurePosixPath]:
    """Собирает целевые пути COPY финального stage."""
    return [
        PurePosixPath(argument.split()[-1])
        for argument in instruction_arguments(final_stage(_instructions()), "COPY")
    ]


def _destination_covers(destinations: list[PurePosixPath], path: PurePosixPath) -> bool:
    """Проверяет, что путь лежит в скопированном дереве."""
    return any(path == dest or path.is_relative_to(dest) for dest in destinations)


def test_dockerfile_uses_at_least_two_stages() -> None:
    """Проверяет multi-stage сборку."""
    from_instructions = instruction_arguments(_instructions(), "FROM")
    assert len(from_instructions) >= 2


def test_final_stage_runs_as_non_root() -> None:
    """Проверяет, что процесс финального stage не root."""
    users = instruction_arguments(final_stage(_instructions()), "USER")
    assert users
    assert users[-1].split(":")[0] not in {"root", "0"}


def test_image_syncs_frozen_lockfile_without_dev_group() -> None:
    """Проверяет pinned lockfile и отсутствие dev-группы в образе."""
    dockerfile = _dockerfile()
    assert "dependency-groups.dev" not in dockerfile
    sync_commands = [
        argument
        for name, argument in _instructions()
        if name == "RUN" and "uv sync" in argument
    ]
    assert sync_commands
    for command in sync_commands:
        assert "--frozen" in command
        assert "--no-dev" in command
        assert "--group dev" not in command
        assert "--dev" not in command.replace("--no-dev", "")


def test_final_stage_does_not_copy_git_metadata() -> None:
    """Проверяет, что финальный stage не копирует каталог .git."""
    copies = instruction_arguments(final_stage(_instructions()), "COPY")
    assert all(".git" not in argument.split() for argument in copies)


def test_dockerfile_does_not_embed_provider_key() -> None:
    """Проверяет отсутствие ключа провайдера в инструкции образа."""
    assert "DEEPSEEK_API_KEY" not in _dockerfile()


def test_container_process_loads_published_asgi_app() -> None:
    """Проверяет, что CMD запускает skillhub.main:app на сети контейнера."""
    commands = instruction_arguments(_instructions(), "CMD")
    assert commands
    command = commands[-1]
    assert "skillhub.main:app" in command
    assert _CONTAINER_BIND in command
    assert "-m skillhub" not in command
    assert "127.0.0.1" not in command


def test_healthcheck_gets_local_health_inside_container() -> None:
    """Проверяет read-only GET /health на 127.0.0.1 внутри контейнера."""
    checks = instruction_arguments(_instructions(), "HEALTHCHECK")
    assert checks
    check = checks[-1]
    assert "127.0.0.1" in check
    assert "/health" in check
    assert _CONTAINER_BIND not in check
    assert "POST" not in check
    assert "DEEPSEEK_API_KEY" not in check


def test_runtime_layout_keeps_tariffs_at_app_factory_parents() -> None:
    """Проверяет, что runtime несёт исходники или тарифы согласованно с parents[2]."""
    project_syncs = _project_sync_commands()
    assert project_syncs
    destinations = _runtime_copy_destinations()
    editable = all("--no-editable" not in command for command in project_syncs)
    app_factory = _EDITABLE_APP_FACTORY if editable else _WHEEL_APP_FACTORY
    tariffs = app_factory.parents[2] / _TARIFFS
    assert _destination_covers(destinations, app_factory)
    if editable:
        assert _destination_covers(destinations, tariffs)
        return
    site_tariffs = app_factory.parents[2] / "config"
    assert any(
        dest in (site_tariffs, tariffs) or dest.is_relative_to(site_tariffs)
        for dest in destinations
    )
