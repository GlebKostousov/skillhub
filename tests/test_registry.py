"""Проверяет публичный фасад безопасного файлового реестра."""

import ctypes
import importlib
import json
import os
import struct
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest
import yaml
from structlog.testing import capture_logs

import skillhub.registry._filesystem as filesystem
from skillhub.core import configure_logging
from skillhub.registry import (
    MAX_SKILL_FILE_BYTES,
    LoadIssue,
    LoadReport,
    Skill,
    SkillRegistry,
)
from skillhub.registry._platform_types import (
    Handle,
    PlatformAdapter,
    UnsafePathError,
)
from skillhub.registry._resolution import (
    MAX_LINK_HOPS,
    MAX_RESOLUTION_STEPS,
    ResolutionBudget,
)


def _write_skill(
    directory: Path,
    *,
    name: str | None = "valid-skill",
    caption: str = "Useful mode",
    description: str | None = "Checks safe loading.",
    body: str = "# Instruction\n\nProcess the material.",
) -> Path:
    """Записывает тестовый SKILL.md через публичный файловый формат.

    Args:
        directory: каталог тестового скилла.
        name: имя в метаданных или отсутствие поля.
        caption: подпись режима.
        description: описание или отсутствие обязательного поля.
        body: Markdown-тело после front matter.

    Returns:
        Путь записанного файла.
    """
    directory.mkdir(parents=True, exist_ok=True)
    metadata = [f"caption: {caption}"]
    if name is not None:
        metadata.append(f"name: {name}")
    if description is not None:
        metadata.append(f"description: {description}")
    content = f"---\n{'\n'.join(metadata)}\n---\n{body}"
    skill_file = directory / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def _issue_reasons(report: LoadReport) -> tuple[str, ...]:
    """Возвращает машинные причины пропуска из отчёта.

    Args:
        report: результат публичной загрузки реестра.

    Returns:
        Причины в детерминированном порядке.
    """
    return tuple(issue.reason for issue in report.issues)


def _try_symlink(source: Path, link: Path) -> None:
    """Создаёт символическую ссылку или фиксирует отсутствие возможности.

    Args:
        source: существующая цель ссылки.
        link: создаваемый путь ссылки.
    """
    try:
        link.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        error_code = getattr(exc, "winerror", None) or exc.errno
        pytest.skip(f"Создание symlink недоступно: {error_code}")


def _create_directory_link(source: Path, link: Path) -> None:
    """Создаёт доступную на текущей ОС ссылку на каталог.

    Args:
        source: существующий каталог назначения.
        link: создаваемая ссылка или точка соединения каталогов.
    """
    if os.name != "nt":
        link.symlink_to(source, target_is_directory=True)
        return
    result = subprocess.run(  # noqa: S603 - аргументы формируются тестом
        [
            os.environ["COMSPEC"],
            "/d",
            "/c",
            "mklink",
            "/J",
            str(link),
            str(source),
        ],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0


def _write_sized_skill(directory: Path, name: str, size: int) -> None:
    """Записывает валидный скилл точного байтового размера.

    Args:
        directory: каталог тестового скилла.
        name: каноническое имя.
        size: итоговый размер файла.
    """
    directory.mkdir(parents=True)
    prefix = (
        f"---\nname: {name}\ncaption: Limit\ndescription: Boundary\n---\n"
    ).encode()
    assert len(prefix) < size
    (directory / "SKILL.md").write_bytes(prefix + b"x" * (size - len(prefix)))


def _platform_adapter() -> PlatformAdapter:
    """Возвращает приватный платформенный адаптер для проверки отказов."""
    return cast("PlatformAdapter", vars(filesystem)["_backend"])


def test_valid_skill_and_report_are_immutable(tmp_path: Path) -> None:
    """Проверяет валидную модель и неизменяемость публичных значений."""
    _write_skill(tmp_path / "catalog-entry")

    report = SkillRegistry(tmp_path).load()

    assert report == LoadReport(
        skills=(
            Skill(
                name="valid-skill",
                caption="Useful mode",
                description="Checks safe loading.",
                body="# Instruction\n\nProcess the material.",
                has_files=False,
            ),
        ),
        issues=(),
    )
    skill_attribute = "name"
    report_attribute = "issues"
    with pytest.raises(FrozenInstanceError):
        setattr(report.skills[0], skill_attribute, "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(report, report_attribute, ())
    with pytest.raises(TypeError):
        cast("list[Skill]", report.skills)[0] = report.skills[0]


@pytest.mark.parametrize(
    ("directory_name", "metadata_name", "expected_reason"),
    [
        ("fallback-name", None, None),
        ("Bad_Fallback", None, "invalid_name"),
        ("a" * 65, None, "invalid_name"),
        ("unused", "a" * 64, None),
        ("unused", "a" * 65, "invalid_name"),
        ("unused", "Not-Kebab", "invalid_name"),
        ("unused", "double--hyphen", "invalid_name"),
    ],
)
def test_name_fallback_format_and_length_edges(
    tmp_path: Path,
    directory_name: str,
    metadata_name: str | None,
    expected_reason: str | None,
) -> None:
    """Проверяет запасное имя, kebab-case и границу 64 символа."""
    _write_skill(tmp_path / directory_name, name=metadata_name)

    report = SkillRegistry(tmp_path).load()

    if expected_reason is None:
        expected_name = metadata_name or directory_name
        assert tuple(skill.name for skill in report.skills) == (expected_name,)
        assert report.issues == ()
    else:
        assert report.skills == ()
        assert _issue_reasons(report) == (expected_reason,)


@pytest.mark.parametrize(
    ("description", "expected_reason"),
    [
        (None, "missing_description"),
        ("", "missing_description"),
        (" " * 4, "missing_description"),
        ("д" * 1_024, None),
        ("д" * 1_025, "description_too_long"),
    ],
)
def test_description_is_required_and_bounded(
    tmp_path: Path,
    description: str | None,
    expected_reason: str | None,
) -> None:
    """Проверяет обязательность description и границу 1024 символа."""
    _write_skill(tmp_path / "described", description=description)

    report = SkillRegistry(tmp_path).load()

    assert _issue_reasons(report) == (
        () if expected_reason is None else (expected_reason,)
    )
    assert len(report.skills) == (1 if expected_reason is None else 0)


@pytest.mark.parametrize("body", ["", " ", "\n\t\r\n"])
def test_whitespace_only_body_is_rejected(tmp_path: Path, body: str) -> None:
    """Проверяет отказ для пустого Markdown-тела."""
    _write_skill(tmp_path / "empty-body", body=body)

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("empty_body",)


def test_body_is_returned_without_front_matter(tmp_path: Path) -> None:
    """Проверяет возврат непустого Markdown без служебных разделителей."""
    _write_skill(tmp_path / "body", body="\n# Heading\n\nText.\n")

    report = SkillRegistry(tmp_path).load()

    assert report.skills[0].body == "# Heading\n\nText."


@pytest.mark.parametrize(
    "front_matter",
    [
        "name: [broken",
        "- not\n- a\n- mapping",
        "name: valid-skill\ncaption: Режим\ndescription: 42",
    ],
)
def test_malformed_or_wrongly_typed_yaml_is_rejected(
    tmp_path: Path,
    front_matter: str,
) -> None:
    """Проверяет отказ для битого или неподходящего YAML."""
    directory = tmp_path / "broken-yaml"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\n{front_matter}\n---\nBody",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_yaml",)


@pytest.mark.parametrize(
    "content",
    [
        "name: missing-opening\ndescription: Broken\n---\nBody",
        "---\nname: missing-closing\ndescription: Broken\nBody",
    ],
)
def test_missing_front_matter_delimiter_is_rejected(
    tmp_path: Path,
    content: str,
) -> None:
    """Проверяет обязательность обоих разделителей front matter."""
    directory = tmp_path / "missing-delimiter"
    directory.mkdir()
    (directory / "SKILL.md").write_text(content, encoding="utf-8")

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_yaml",)


@pytest.mark.parametrize(
    ("front_matter", "expected_reason"),
    [
        ("name: 42\ncaption: Mode\ndescription: Broken", "invalid_name"),
        ("name: wrong-caption\ncaption: [Mode]\ndescription: Broken", "invalid_yaml"),
    ],
)
def test_wrong_typed_metadata_does_not_interrupt_valid_sibling(
    tmp_path: Path,
    front_matter: str,
    expected_reason: str,
) -> None:
    """Проверяет изоляцию неверных типов name и caption."""
    _write_skill(tmp_path / "alpha", name="alpha")
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\n{front_matter}\n---\nBody",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("alpha",)
    assert _issue_reasons(report) == (expected_reason,)


def test_unsafe_python_yaml_tag_is_not_executed(tmp_path: Path) -> None:
    """Проверяет безопасный отказ без исполнения Python-тега."""
    marker = tmp_path / "executed-marker"
    expression = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    directory = tmp_path / "unsafe-yaml"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\n"
        "name: unsafe-yaml\n"
        "caption: Режим\n"
        f"description: !!python/object/apply:builtins.eval [{json.dumps(expression)}]\n"
        "---\n"
        "Тело",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_yaml",)
    assert not marker.exists()


def test_yaml_graph_tokens_are_rejected(tmp_path: Path) -> None:
    """Проверяет отказ от anchor/alias-графа до построения YAML-объекта."""
    directory = tmp_path / "graph-yaml"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\n"
        "name: graph-yaml\n"
        "caption: *shared\n"
        "description: &shared Shared value\n"
        "---\n"
        "Body",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_yaml",)


def test_deeply_broken_yaml_does_not_interrupt_other_directories(
    tmp_path: Path,
) -> None:
    """Проверяет изоляцию YAML, превышающего глубину Python-парсера."""
    _write_skill(tmp_path / "valid", name="valid")
    directory = tmp_path / "deep-yaml"
    directory.mkdir()
    nested_value = "[" * 2_000 + "value" + "]" * 2_000
    (directory / "SKILL.md").write_text(
        f"---\nname: deep-yaml\ncaption: Deep\ndescription: {nested_value}\n---\nBody",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("valid",)
    assert _issue_reasons(report) == ("invalid_yaml",)


def test_invalid_utf8_is_rejected_before_yaml_parsing(tmp_path: Path) -> None:
    """Проверяет строгий UTF-8 до разбора YAML."""
    directory = tmp_path / "invalid-utf8"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(b"---\ndescription: \xff\n---\nbody")

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_utf8",)


def test_oversized_file_is_rejected_before_content_validation(
    tmp_path: Path,
) -> None:
    """Проверяет публичный байтовый лимит до декодирования и YAML."""
    assert MAX_SKILL_FILE_BYTES == 65_536
    private_marker = "oversize-private-marker-9381"
    directory = tmp_path / "oversized"
    directory.mkdir()
    prefix = (
        "---\n"
        "name: oversized\n"
        "caption: Режим\n"
        f"description: !!python/object/apply:builtins.eval [{private_marker}]\n"
        "---\n"
    ).encode()
    (directory / "SKILL.md").write_bytes(
        prefix + b"\xff" + b"x" * (MAX_SKILL_FILE_BYTES - len(prefix)),
    )

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("file_too_large",)
    assert private_marker not in repr(report)


def test_file_at_exact_byte_limit_is_accepted(tmp_path: Path) -> None:
    """Проверяет включительную границу публичного байтового лимита."""
    directory = tmp_path / "exact-limit"
    directory.mkdir()
    prefix = b"---\nname: exact-limit\ncaption: Exact\ndescription: Boundary\n---\n"
    payload = prefix + b"x" * (MAX_SKILL_FILE_BYTES - len(prefix))
    assert len(payload) == 65_536
    (directory / "SKILL.md").write_bytes(payload)

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("exact-limit",)
    assert report.issues == ()


def test_short_reads_continue_on_the_same_open_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет полное ограниченное чтение с одного дескриптора."""
    skill_file = _write_skill(tmp_path / "short-read", name="short-read")
    payload = skill_file.read_bytes()
    chunks = [payload[:11], payload[11:], b""]
    observed_tokens: list[int] = []

    def short_read(handle: Handle, size: int) -> bytes:
        observed_tokens.append(handle.token)
        return chunks.pop(0)[:size]

    monkeypatch.setattr(_platform_adapter(), "read", short_read)

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("short-read",)
    assert len(set(observed_tokens)) == 1
    assert len(observed_tokens) == 3


def test_one_broken_directory_does_not_hide_valid_skills(tmp_path: Path) -> None:
    """Проверяет изоляцию одной ошибочной попытки загрузки."""
    _write_skill(tmp_path / "alpha", name="alpha")
    _write_skill(tmp_path / "broken", name="broken", description=None)
    _write_skill(tmp_path / "zeta", name="zeta")

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("alpha", "zeta")
    assert _issue_reasons(report) == ("missing_description",)


def test_repeated_load_has_deterministic_skill_and_issue_order(
    tmp_path: Path,
) -> None:
    """Проверяет одинаковые данные и порядок при повторном чтении."""
    _write_skill(tmp_path / "zeta", name="zeta")
    _write_skill(tmp_path / "charlie", name="charlie", description=None)
    _write_skill(tmp_path / "alpha", name="alpha")
    (tmp_path / "bravo").mkdir()

    registry = SkillRegistry(tmp_path)

    first = registry.load()
    second = registry.load()

    assert first == second
    assert tuple(skill.name for skill in first.skills) == ("alpha", "zeta")
    assert first.issues == (
        LoadIssue(path="bravo", reason="missing_file"),
        LoadIssue(path="charlie", reason="missing_description"),
    )


def test_has_files_uses_nested_files_but_not_empty_directories(
    tmp_path: Path,
) -> None:
    """Проверяет рекурсивные файлы и игнорирование пустых каталогов."""
    nested = tmp_path / "with-file"
    _write_skill(nested, name="with-file")
    (nested / "references").mkdir()
    (nested / "references" / "note.md").write_text("data", encoding="utf-8")
    empty = tmp_path / "empty-directories"
    _write_skill(empty, name="empty-directories")
    (empty / "references" / "nested").mkdir(parents=True)

    report = SkillRegistry(tmp_path).load()

    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("empty-directories", False),
        ("with-file", True),
    )


def test_nested_skill_md_counts_as_an_additional_file(tmp_path: Path) -> None:
    """Проверяет исключение только корневого SKILL.md скилла."""
    directory = tmp_path / "nested-skill-file"
    _write_skill(directory, name="nested-skill-file")
    nested_file = directory / "references" / "SKILL.md"
    nested_file.parent.mkdir()
    nested_file.write_text("data", encoding="utf-8")

    report = SkillRegistry(tmp_path).load()

    assert report.skills[0].has_files is True


def test_internal_skill_file_symlink_is_allowed(tmp_path: Path) -> None:
    """Проверяет чтение внутренней ссылки после containment-проверки."""
    shared = tmp_path / "shared-skill.md"
    _write_skill(tmp_path / "source", name="internal-link")
    (tmp_path / "source" / "SKILL.md").replace(shared)
    _try_symlink(shared, tmp_path / "source" / "SKILL.md")

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("internal-link",)
    assert report.issues == ()


def test_opened_internal_file_is_allowed_without_symlink_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет внутреннюю цель открытого дескриптора независимо от ОС."""
    root = tmp_path / "skills"
    directory = root / "redirected"
    directory.mkdir(parents=True)
    shared = root / "shared.md"
    source = _write_skill(tmp_path / "source", name="internal-link")
    source.replace(shared)
    backend = _platform_adapter()
    original_open_child = backend.open_child

    def redirect_skill_file(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        """Подменяет только открытый внутренний SKILL.md."""
        if name == "SKILL.md":
            return backend.open_path(shared)
        return original_open_child(root_handle, parent, name)

    monkeypatch.setattr(backend, "open_child", redirect_skill_file)

    report = SkillRegistry(root).load()

    assert tuple(skill.name for skill in report.skills) == ("internal-link",)
    assert report.issues == ()


def test_external_skill_file_symlink_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ для ссылки на файл за корнем реестра."""
    root = tmp_path / "skills"
    directory = root / "external-link"
    directory.mkdir(parents=True)
    external = tmp_path / "outside.md"
    _write_skill(tmp_path / "outside-source", name="outside")
    (tmp_path / "outside-source" / "SKILL.md").replace(external)
    _try_symlink(external, directory / "SKILL.md")

    report = SkillRegistry(root).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("unsafe_path",)


def test_opened_escape_is_rejected_without_symlink_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет внешнюю цель дескриптора независимо от символических ссылок."""
    root = tmp_path / "skills"
    _write_skill(root / "redirected", name="redirected")
    external = tmp_path / "outside.md"
    external.write_text("outside-private-content", encoding="utf-8")
    backend = _platform_adapter()
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

    report = SkillRegistry(root).load()

    assert report.skills == ()
    assert _issue_reasons(report) == ("unsafe_path",)
    assert "outside-private-content" not in repr(report)


def test_external_file_symlink_is_not_counted_by_has_files(tmp_path: Path) -> None:
    """Проверяет запрет обхода внешней ссылки при подсчёте файлов."""
    root = tmp_path / "skills"
    directory = root / "safe-skill"
    _write_skill(directory, name="safe-skill")
    external = tmp_path / "outside.txt"
    external.write_text("outside-private-content", encoding="utf-8")
    _try_symlink(external, directory / "reference.txt")

    report = SkillRegistry(root).load()

    assert report.skills[0].has_files is False
    assert "outside-private-content" not in repr(report)


def test_skip_issue_and_warning_never_disclose_untrusted_content(
    tmp_path: Path,
) -> None:
    """Проверяет безопасные поля отчёта и предупреждения."""
    private_marker = "private-path-and-yaml-marker-4817"
    root = tmp_path / "skills"
    hostile_directory = root / f"{private_marker} = value"
    _write_skill(
        hostile_directory,
        name="broken",
        description=f"[{private_marker}",
        body=f"secret body {private_marker}",
    )

    with capture_logs() as logs:
        report = SkillRegistry(root).load()

    observed = f"{report!r}{logs!r}"
    assert report.skills == ()
    assert _issue_reasons(report) == ("invalid_yaml",)
    assert report.issues[0].path.startswith("unsafe-")
    assert not Path(report.issues[0].path).is_absolute()
    assert "/" not in report.issues[0].path
    assert "\\" not in report.issues[0].path
    assert all(
        character.isascii()
        and (character.isalnum() or character in "._-")
        and not character.isspace()
        for character in report.issues[0].path
    )
    assert private_marker not in observed
    assert str(tmp_path) not in observed
    assert "secret body" not in observed
    assert "traceback" not in observed.lower()
    assert logs == [
        {
            "event": "registry.skill_skipped",
            "error_code": "invalid_yaml",
            "skill_path": report.issues[0].path,
            "log_level": "warning",
        }
    ]


def test_skip_warning_renders_safe_path_and_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Проверяет безопасные диагностические значения итогового JSON."""
    configure_logging("test")
    _write_skill(tmp_path / "broken", name="broken", description=None)

    report = SkillRegistry(tmp_path).load()

    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "registry.skill_skipped"
    assert event["error_code"] == "missing_description"
    assert event["skill_path"] == report.issues[0].path


def test_missing_and_empty_roots_have_explicit_behavior(tmp_path: Path) -> None:
    """Проверяет пустой результат и стабильную причину отсутствующего корня."""
    missing = SkillRegistry(tmp_path / "missing").load()
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty = SkillRegistry(empty_root).load()

    assert missing == LoadReport(
        skills=(),
        issues=(LoadIssue(path=".", reason="root_missing"),),
    )
    assert empty == LoadReport(skills=(), issues=())


@pytest.mark.parametrize(
    "extra_value",
    [
        "2026-99-99",
        "9" * 5_000,
    ],
)
def test_yaml_constructor_errors_are_isolated(
    tmp_path: Path,
    extra_value: str,
) -> None:
    """Проверяет изоляцию ожидаемых ошибок конструкторов безопасного YAML."""
    _write_skill(tmp_path / "alpha", name="alpha")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text(
        "---\n"
        "name: broken\n"
        "caption: Broken\n"
        "description: Broken metadata\n"
        f"extra: {extra_value}\n"
        "---\n"
        "Body",
        encoding="utf-8",
    )

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("alpha",)
    assert report.issues == (LoadIssue(path="broken", reason="invalid_yaml"),)


def test_yaml_overflow_error_is_isolated_from_valid_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет нормализацию OverflowError конструктора безопасного YAML."""
    _write_skill(tmp_path / "alpha", name="alpha")
    _write_skill(
        tmp_path / "broken",
        name="broken",
        description="overflow-constructor-trigger",
    )
    original_safe_load = yaml.safe_load

    def overflow_on_marker(stream: str) -> object:
        if "overflow-constructor-trigger" in stream:
            raise OverflowError
        return original_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", overflow_on_marker)

    report = SkillRegistry(tmp_path).load()

    assert tuple(skill.name for skill in report.skills) == ("alpha",)
    assert report.issues == (LoadIssue(path="broken", reason="invalid_yaml"),)


def test_duplicate_canonical_name_uses_sorted_first_directory(
    tmp_path: Path,
) -> None:
    """Проверяет детерминированный выбор первого каталога при коллизии."""
    _write_skill(
        tmp_path / "alpha-directory",
        name="shared-name",
        caption="First",
    )
    _write_skill(
        tmp_path / "zeta-directory",
        name="shared-name",
        caption="Second",
    )

    report = SkillRegistry(tmp_path).load()

    assert tuple((skill.name, skill.caption) for skill in report.skills) == (
        ("shared-name", "First"),
    )
    assert report.issues == (LoadIssue(path="zeta-directory", reason="duplicate_name"),)


def test_root_entry_limit_has_inclusive_edge_and_fail_closed_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет ограниченное перечисление корня без частичного результата."""
    expected_limit = 256
    exact_root = tmp_path / "exact"
    exact_root.mkdir()
    for index in range(expected_limit):
        (exact_root / f"entry-{index:03}.txt").write_text("", encoding="utf-8")
    assert SkillRegistry(exact_root).load() == LoadReport(skills=(), issues=())

    overflow_root = tmp_path / "overflow"
    for index in range(expected_limit + 1):
        _write_skill(
            overflow_root / f"skill-{index:03}",
            name=f"skill-{index:03}",
        )
    parse_calls = 0
    original_safe_load = yaml.safe_load

    def counted_safe_load(stream: str) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counted_safe_load)

    report = SkillRegistry(overflow_root).load()

    assert report == LoadReport(
        skills=(),
        issues=(LoadIssue(path=".", reason="registry_limit_exceeded"),),
    )
    assert parse_calls == 0


def test_skill_count_limit_bounds_parser_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет включительную границу числа обрабатываемых скиллов."""
    expected_limit = 128
    for index in range(expected_limit + 1):
        _write_skill(tmp_path / f"skill-{index:03}", name=f"skill-{index:03}")
    parse_calls = 0
    original_safe_load = yaml.safe_load

    def counted_safe_load(stream: str) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counted_safe_load)

    report = SkillRegistry(tmp_path).load()

    assert len(report.skills) == expected_limit
    assert report.issues == (LoadIssue(path=".", reason="registry_limit_exceeded"),)
    assert parse_calls == expected_limit


def test_total_skill_byte_limit_has_inclusive_edge_and_bounds_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет суммарный предел байтов до декодирования и разбора."""
    expected_limit = 1_048_576
    exact_count = expected_limit // MAX_SKILL_FILE_BYTES
    assert expected_limit % MAX_SKILL_FILE_BYTES == 0
    for index in range(exact_count):
        name = f"skill-{index:02}"
        _write_sized_skill(
            tmp_path / name,
            name,
            MAX_SKILL_FILE_BYTES,
        )
    _write_skill(tmp_path / "zz-overflow", name="zz-overflow")
    parse_calls = 0
    original_safe_load = yaml.safe_load

    def counted_safe_load(stream: str) -> object:
        nonlocal parse_calls
        parse_calls += 1
        return original_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counted_safe_load)

    report = SkillRegistry(tmp_path).load()

    assert len(report.skills) == exact_count
    assert report.issues == (LoadIssue(path=".", reason="registry_limit_exceeded"),)
    assert parse_calls == exact_count


def test_parent_swap_cannot_read_opened_external_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет конечный путь дескриптора после подмены родителя."""
    root = tmp_path / "skills"
    victim = root / "victim"
    _write_skill(victim, name="original")
    detached = tmp_path / "detached"
    outside = tmp_path / "outside"
    private_marker = "outside-after-check-private-body"
    _write_skill(outside, name="outside", body=private_marker)
    backend = _platform_adapter()
    original_open_child = backend.open_child
    swapped = False

    def swapping_open_child(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        nonlocal swapped
        if name == "SKILL.md" and not swapped:
            victim.rename(detached)
            _create_directory_link(outside, victim)
            swapped = True
        return original_open_child(root_handle, parent, name)

    monkeypatch.setattr(backend, "open_child", swapping_open_child)

    report = SkillRegistry(root).load()

    assert swapped is True
    assert private_marker not in repr(report)
    if os.name == "nt":
        assert report.skills == ()
        assert _issue_reasons(report) == ("unsafe_path",)
    else:
        assert tuple(skill.name for skill in report.skills) == ("original",)


@pytest.mark.skipif(os.name != "nt", reason="Проверка требует Windows")
def test_windows_child_open_stays_bound_to_held_parent_after_swap(
    tmp_path: Path,
) -> None:
    """Проверяет относительное открытие после замены пути родителя."""
    root_path = tmp_path / "skills"
    victim = root_path / "victim"
    original_marker = "original-held-parent-body"
    attacker_marker = "replacement-path-private-body"
    _write_skill(victim, name="original", body=original_marker)
    backend = _platform_adapter()
    root = backend.open_path(root_path)
    parent = backend.open_child(root, root, "victim")
    detached = tmp_path / "detached"
    child: Handle | None = None
    try:
        victim.rename(detached)
        _write_skill(victim, name="replacement", body=attacker_marker)

        child = backend.open_child(root, parent, "SKILL.md")
        payload = backend.read(child, MAX_SKILL_FILE_BYTES)

        assert original_marker.encode() in payload
        assert attacker_marker.encode() not in payload
    finally:
        if child is not None:
            backend.close(child)
        backend.close(parent)
        backend.close(root)


@pytest.mark.skipif(os.name != "nt", reason="Проверка требует Windows junction")
def test_root_swap_to_external_junction_stops_before_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет привязку открытого корня к разрешённому заранее пути."""
    root = tmp_path / "skills"
    _write_skill(root / "trusted", name="trusted")
    detached = tmp_path / "detached"
    outside = tmp_path / "outside"
    private_marker = "root-swap-private-marker-9173"
    _write_skill(outside / "attacker", name="attacker", body=private_marker)
    backend = _platform_adapter()
    original_open_path = backend.open_path
    original_iter_entries = backend.iter_entries
    original_read = backend.read
    swapped = False
    external_enumerated = False
    external_read = False

    def swapping_open_path(path: Path) -> Handle:
        nonlocal swapped
        root.rename(detached)
        _create_directory_link(outside, root)
        swapped = True
        return original_open_path(path)

    def guarded_entries(handle: Handle) -> Iterator[str]:
        nonlocal external_enumerated
        if handle.final_path == outside.resolve():
            external_enumerated = True
        return original_iter_entries(handle)

    def guarded_read(handle: Handle, size: int) -> bytes:
        nonlocal external_read
        if outside.resolve() in (handle.final_path, *handle.final_path.parents):
            external_read = True
        return original_read(handle, size)

    monkeypatch.setattr(backend, "open_path", swapping_open_path)
    monkeypatch.setattr(backend, "iter_entries", guarded_entries)
    monkeypatch.setattr(backend, "read", guarded_read)

    report = SkillRegistry(root).load()

    assert swapped is True
    assert external_enumerated is False
    assert external_read is False
    assert private_marker not in repr(report)
    assert report == LoadReport(
        skills=(),
        issues=(LoadIssue(path=".", reason="root_unreadable"),),
    )


def test_opened_root_path_mismatch_stops_before_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет конечный путь того же дескриптора до перечисления корня."""
    root = tmp_path / "skills"
    _write_skill(root / "trusted", name="trusted")
    outside = tmp_path / "outside"
    private_marker = "opened-root-private-marker-4129"
    _write_skill(outside / "attacker", name="attacker", body=private_marker)
    backend = _platform_adapter()
    original_open_path = backend.open_path
    original_iter_entries = backend.iter_entries
    original_read = backend.read
    external_enumerated = False
    external_read = False

    def mismatched_open_path(_path: Path) -> Handle:
        return original_open_path(outside)

    def guarded_entries(handle: Handle) -> Iterator[str]:
        nonlocal external_enumerated
        if handle.final_path == outside.resolve():
            external_enumerated = True
        return original_iter_entries(handle)

    def guarded_read(handle: Handle, size: int) -> bytes:
        nonlocal external_read
        if outside.resolve() in (handle.final_path, *handle.final_path.parents):
            external_read = True
        return original_read(handle, size)

    monkeypatch.setattr(backend, "open_path", mismatched_open_path)
    monkeypatch.setattr(backend, "iter_entries", guarded_entries)
    monkeypatch.setattr(backend, "read", guarded_read)

    report = SkillRegistry(root).load()

    assert external_enumerated is False
    assert external_read is False
    assert private_marker not in repr(report)
    assert report == LoadReport(
        skills=(),
        issues=(LoadIssue(path=".", reason="root_unreadable"),),
    )


def test_opened_external_file_handle_is_rejected_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет независимый от ОС запрет внешнего дескриптора."""
    root = tmp_path / "skills"
    _write_skill(root / "skill", name="skill")
    outside = tmp_path / "outside.md"
    outside.write_text("outside-private-handle-body", encoding="utf-8")
    backend = _platform_adapter()
    original_open_child = backend.open_child
    external_opened = False
    read_attempted = False

    def external_file_open(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        nonlocal external_opened
        if name != "SKILL.md":
            return original_open_child(root_handle, parent, name)
        external_opened = True
        return Handle(
            token=-1,
            final_path=outside.resolve(),
            identity=(999, 1),
            is_directory=False,
            is_regular=True,
        )

    def forbidden_read(_handle: Handle, _size: int) -> bytes:
        nonlocal read_attempted
        read_attempted = True
        return b"outside-private-handle-body"

    monkeypatch.setattr(backend, "open_child", external_file_open)
    monkeypatch.setattr(backend, "read", forbidden_read)

    report = SkillRegistry(root).load()

    assert external_opened is True
    assert read_attempted is False
    assert report.skills == ()
    assert _issue_reasons(report) == ("unsafe_path",)
    assert "outside-private-handle-body" not in repr(report)


def test_internal_directory_link_counts_nested_regular_file(
    tmp_path: Path,
) -> None:
    """Проверяет единое правило для внутренней ссылки на каталог."""
    root = tmp_path / "skills"
    source = root / "alpha-source"
    target = root / "zeta-target"
    _write_skill(source, name="alpha-source")
    _write_skill(target, name="zeta-target")
    target_files = target / "actual-files"
    target_files.mkdir()
    (target_files / "note.md").write_text("data", encoding="utf-8")
    _create_directory_link(target_files, source / "references")

    report = SkillRegistry(root).load()

    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("alpha-source", True),
        ("zeta-target", True),
    )
    assert report.issues == ()


def test_root_directory_alias_is_processed_once(tmp_path: Path) -> None:
    """Проверяет дедупликацию корневых ссылок по файловой идентичности."""
    root = tmp_path / "skills"
    target = root / "target"
    _write_skill(target, name="shared-target")
    _create_directory_link(target, root / "alias")

    report = SkillRegistry(root).load()

    assert tuple(skill.name for skill in report.skills) == ("shared-target",)
    assert report.issues == ()


def test_internal_auxiliary_file_link_counts_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет единое правило для внутренней ссылки на обычный файл."""
    root = tmp_path / "skills"
    source = root / "skill"
    _write_skill(source, name="skill")
    shared = root / "shared.txt"
    shared.write_text("data", encoding="utf-8")
    if os.name != "nt":
        (source / "reference.txt").symlink_to(shared)
    else:
        backend = _platform_adapter()
        original_iter_entries = backend.iter_entries
        original_open_child = backend.open_child

        def linked_entries(handle: Handle) -> Iterator[str]:
            if handle.final_path.name == "skill":
                return iter(("SKILL.md", "reference.txt"))
            return original_iter_entries(handle)

        def linked_file_open(
            root_handle: Handle,
            parent: Handle,
            name: str,
        ) -> Handle:
            if parent.final_path.name == "skill" and name == "reference.txt":
                return backend.open_path(shared)
            return original_open_child(root_handle, parent, name)

        monkeypatch.setattr(backend, "iter_entries", linked_entries)
        monkeypatch.setattr(backend, "open_child", linked_file_open)

    report = SkillRegistry(root).load()

    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("skill", True),
    )
    assert report.issues == ()


def test_external_directory_junction_is_not_enumerated(
    tmp_path: Path,
) -> None:
    """Проверяет запрет обхода внешней связи каталогов или файловой ссылки."""
    root = tmp_path / "skills"
    source = root / "safe-skill"
    _write_skill(source, name="safe-skill")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside-private-tree", encoding="utf-8")
    _create_directory_link(outside, source / "references")

    report = SkillRegistry(root).load()

    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("safe-skill", False),
    )
    assert report.issues == ()
    assert "outside-private-tree" not in repr(report)


def test_external_directory_handle_is_skipped_before_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет независимый от ОС запрет перечисления внешней цели."""
    root = tmp_path / "skills"
    source = root / "safe-skill"
    _write_skill(source, name="safe-skill")
    (source / "references").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    backend = _platform_adapter()
    original_open_child = backend.open_child
    original_iter_entries = backend.iter_entries
    external_handle = Handle(
        token=-1,
        final_path=outside.resolve(),
        identity=(999, 2),
        is_directory=True,
        is_regular=False,
    )
    external_opened = False
    external_enumerated = False

    def external_directory_open(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        nonlocal external_opened
        if name != "references":
            return original_open_child(root_handle, parent, name)
        external_opened = True
        return external_handle

    def guarded_iter_entries(
        handle: Handle,
    ) -> Iterator[str]:
        nonlocal external_enumerated
        if handle is external_handle:
            external_enumerated = True
            raise AssertionError
        return original_iter_entries(handle)

    monkeypatch.setattr(backend, "open_child", external_directory_open)
    monkeypatch.setattr(backend, "iter_entries", guarded_iter_entries)

    report = SkillRegistry(root).load()

    assert external_opened is True
    assert external_enumerated is False
    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("safe-skill", False),
    )


def test_auxiliary_entry_limit_has_inclusive_edge(
    tmp_path: Path,
) -> None:
    """Проверяет стабильный отказ только после точной границы обхода."""
    expected_limit = 1_024
    exact = tmp_path / "exact"
    _write_skill(exact, name="exact")
    for index in range(expected_limit):
        (exact / f"empty-{index:04}").mkdir()

    exact_report = SkillRegistry(tmp_path).load()

    assert tuple((skill.name, skill.has_files) for skill in exact_report.skills) == (
        ("exact", False),
    )
    (exact / f"empty-{expected_limit:04}").mkdir()

    overflow_report = SkillRegistry(tmp_path).load()

    assert overflow_report.skills == ()
    assert overflow_report.issues == (
        LoadIssue(path="exact", reason="directory_limit_exceeded"),
    )


def test_auxiliary_depth_limit_has_inclusive_edge(
    tmp_path: Path,
) -> None:
    """Проверяет ограниченную глубину рекурсивного обхода."""
    expected_limit = 32
    directory = tmp_path / "deep"
    _write_skill(directory, name="deep")
    current = directory
    for index in range(expected_limit):
        current = current / f"level-{index:02}"
        current.mkdir()

    exact_report = SkillRegistry(tmp_path).load()

    assert tuple((skill.name, skill.has_files) for skill in exact_report.skills) == (
        ("deep", False),
    )
    (current / "overflow").mkdir()

    overflow_report = SkillRegistry(tmp_path).load()

    assert overflow_report.skills == ()
    assert overflow_report.issues == (
        LoadIssue(path="deep", reason="directory_limit_exceeded"),
    )


def test_internal_directory_link_cycle_uses_visited_identity(
    tmp_path: Path,
) -> None:
    """Проверяет завершение цикла по идентификаторам открытых каталогов."""
    root = tmp_path / "skills"
    directory = root / "cycle"
    _write_skill(directory, name="cycle")
    _create_directory_link(directory, directory / "self")

    report = SkillRegistry(root).load()

    assert tuple((skill.name, skill.has_files) for skill in report.skills) == (
        ("cycle", False),
    )
    assert report.issues == ()


def test_root_and_entry_io_errors_have_stable_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет безопасные причины ошибок открытия и перечисления."""
    root_file = tmp_path / "root-file"
    root_file.write_text("data", encoding="utf-8")
    assert SkillRegistry(root_file).load().issues == (
        LoadIssue(path=".", reason="root_not_directory"),
    )

    root = tmp_path / "skills"
    _write_skill(root / "entry", name="entry")

    def denied_root(_path: Path) -> None:
        raise OSError

    backend = _platform_adapter()
    monkeypatch.setattr(backend, "open_path", denied_root)
    assert SkillRegistry(root).load().issues == (
        LoadIssue(path=".", reason="root_unreadable"),
    )
    monkeypatch.undo()
    backend = _platform_adapter()

    def denied_enumeration(_root: Handle) -> Iterator[str]:
        raise OSError

    monkeypatch.setattr(backend, "iter_entries", denied_enumeration)
    assert SkillRegistry(root).load().issues == (
        LoadIssue(path=".", reason="root_unreadable"),
    )
    monkeypatch.undo()
    backend = _platform_adapter()

    def denied_entry(
        _root: Handle,
        _parent: Handle,
        _name: str,
    ) -> Handle:
        raise OSError

    monkeypatch.setattr(backend, "open_child", denied_entry)
    assert SkillRegistry(root).load().issues == (
        LoadIssue(path="entry", reason="unsafe_path"),
    )


def test_skill_file_and_directory_io_errors_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет стабильные причины ошибок файла и дерева скилла."""
    root = tmp_path / "skills"
    directory = root / "skill"
    _write_skill(directory, name="skill")
    backend = _platform_adapter()
    original_open_child = backend.open_child

    def denied_skill_file(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        if name == "SKILL.md":
            raise PermissionError
        return original_open_child(root_handle, parent, name)

    monkeypatch.setattr(backend, "open_child", denied_skill_file)
    assert SkillRegistry(root).load().issues == (
        LoadIssue(path="skill", reason="file_unreadable"),
    )
    monkeypatch.setattr(backend, "open_child", original_open_child)
    original_iter_entries = backend.iter_entries

    def denied_skill_tree(handle: Handle) -> Iterator[str]:
        if handle.final_path.name == "skill":
            raise OSError
        return original_iter_entries(handle)

    monkeypatch.setattr(backend, "iter_entries", denied_skill_tree)
    assert SkillRegistry(root).load().issues == (
        LoadIssue(path="skill", reason="directory_unreadable"),
    )


def test_directory_instead_of_skill_file_is_rejected(tmp_path: Path) -> None:
    """Проверяет отказ для каталога вместо обычного SKILL.md."""
    directory = tmp_path / "skill"
    (directory / "SKILL.md").mkdir(parents=True)

    report = SkillRegistry(tmp_path).load()

    assert report.skills == ()
    assert report.issues == (LoadIssue(path="skill", reason="missing_file"),)


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_final_path_prefixes_are_normalized() -> None:
    """Проверяет локальные и сетевые формы конечного пути Win32."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    normalize = cast(
        "Callable[[str], Path]",
        vars(windows_io)["_normalized_final_path"],
    )

    assert normalize(r"\\?\C:\root") == Path(r"C:\root")
    assert normalize(r"\\?\UNC\server\share") == Path(r"\\server\share")
    assert normalize(r"C:\root") == Path(r"C:\root")


def test_windows_mount_point_target_parser_uses_substitute_name() -> None:
    """Проверяет разбор связи каталогов без обращения к файловой цели."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    parse_target = cast(
        "Callable[[bytes], tuple[str, bool]]",
        vars(windows_io)["parse_reparse_target"],
    )
    target = r"\??\C:\root\inside"
    encoded = target.encode("utf-16-le")
    body = struct.pack("<HHHH", 0, len(encoded), len(encoded), 0) + encoded
    payload = struct.pack("<IHH", 0xA0000003, len(body), 0) + body

    assert parse_target(payload) == (target, False)


def test_windows_relative_symlink_target_parser_preserves_relative_flag() -> None:
    """Проверяет относительную семантику файловой символической ссылки."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    parse_target = cast(
        "Callable[[bytes], tuple[str, bool]]",
        vars(windows_io)["parse_reparse_target"],
    )
    target = r"..\shared\SKILL.md"
    encoded = target.encode("utf-16-le")
    body = struct.pack("<HHHHI", 0, len(encoded), len(encoded), 0, 1) + encoded
    payload = struct.pack("<IHH", 0xA000000C, len(body), 0) + body

    assert parse_target(payload) == (target, True)


@pytest.mark.parametrize(
    ("tag", "flags"),
    [
        (0xA0000003, 0),
        (0xA000000C, 1),
    ],
)
def test_windows_reparse_parser_accepts_even_nonzero_name_ranges(
    tag: int,
    flags: int,
) -> None:
    """Проверяет допустимые чётные смещения и длины обоих имён."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    parse_target = cast(
        "Callable[[bytes], tuple[str, bool]]",
        vars(windows_io)["parse_reparse_target"],
    )
    relative = bool(flags)
    print_name = r"C:\shown".encode("utf-16-le")
    target = r"..\shared" if relative else r"\??\C:\root\inside"
    substitute_name = target.encode("utf-16-le")
    path_buffer = print_name + substitute_name
    name_fields = (
        len(print_name),
        len(substitute_name),
        0,
        len(print_name),
    )
    if relative:
        body = struct.pack("<HHHHI", *name_fields, flags) + path_buffer
    else:
        body = struct.pack("<HHHH", *name_fields) + path_buffer
    payload = struct.pack("<IHH", tag, len(body), 0) + body

    assert parse_target(payload) == (target, relative)


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_external_reparse_targets_are_rejected_before_follow_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отклонение внешних и служебных целей до открытия."""
    windows_io = importlib.import_module("skillhub.registry._windows_resolver")
    content_open_called = False

    def forbidden_content_open(*_args: object) -> int:
        nonlocal content_open_called
        content_open_called = True
        raise AssertionError

    monkeypatch.setattr(
        windows_io,
        "_probe_relative",
        lambda _parent, name: b"reparse" if name == "link" else None,
    )
    monkeypatch.setattr(windows_io, "_open_relative", forbidden_content_open)

    local_root = tmp_path / "skills"
    unc_root = Path(r"\\server\share\skills")
    cases = (
        (local_root, r"\??\UNC\server\share\skills\private"),
        (local_root, r"\\server\share\skills\private"),
        (local_root, str(tmp_path / "outside" / "private")),
        (unc_root, r"\??\UNC\server\share\private"),
        (unc_root, r"\\server\share\private"),
        (unc_root, r"\??\UNC\server\other\skills\private"),
        (unc_root, r"\\server\other\skills\private"),
        (unc_root, r"\??\UNC\server"),
        (unc_root, r"\\server"),
        (unc_root, r"\Device\Mup\server\share\skills\private"),
        (unc_root, r"\\.\UNC\server\share\skills\private"),
        (unc_root, r"\\?\UNC\server\share\skills\private"),
        (unc_root, r"\??\GLOBALROOT\Device\Mup\server\share\skills\private"),
    )
    for root_path, target in cases:
        root = Handle(
            token=-1,
            final_path=root_path,
            identity=(1, 1),
            is_directory=True,
            is_regular=False,
        )
        parent = Handle(
            token=-2,
            final_path=root_path / "skill",
            identity=(1, 2),
            is_directory=True,
            is_regular=False,
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(
                windows_io,
                "parse_reparse_target",
                lambda _data, value=target: (value, False),
            )
            with pytest.raises(UnsafePathError):
                windows_io.open_child(root, parent, "link")

    assert content_open_called is False


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_trusted_unc_child_uses_native_relative_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет открытие потомка доверенного корня UNC без сетевого запроса."""
    windows_io = importlib.import_module("skillhub.registry._windows_resolver")
    root = Handle(
        token=1,
        final_path=Path(r"\\server\share\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )
    parent = Handle(
        token=2,
        final_path=Path(r"\\server\share\skills\skill"),
        identity=(1, 2),
        is_directory=True,
        is_regular=False,
    )
    child = Handle(
        token=4,
        final_path=Path(r"\\server\share\skills\skill\SKILL.md"),
        identity=(1, 3),
        is_directory=False,
        is_regular=True,
    )
    observed: list[tuple[Handle, str, int]] = []

    def relative_open(open_parent: Handle, name: str, access: int) -> int:
        observed.append((open_parent, name, access))
        return 3 if len(observed) == 1 else 4

    def relative_probe(open_parent: Handle, name: str) -> None:
        relative_open(open_parent, name, 0)

    monkeypatch.setattr(windows_io, "_probe_relative", relative_probe)
    monkeypatch.setattr(windows_io, "_open_relative", relative_open)
    monkeypatch.setattr(
        windows_io,
        "_reparse_data_from_open_token",
        lambda _token: None,
    )
    monkeypatch.setattr(windows_io, "_make_handle", lambda _token: child)
    monkeypatch.setattr(windows_io, "_CloseHandle", lambda _token: True)

    assert windows_io.open_child(root, parent, "SKILL.md") is child
    assert [(item[0], item[1]) for item in observed] == [
        (parent, "SKILL.md"),
        (parent, "SKILL.md"),
    ]


@pytest.mark.parametrize(
    "target",
    [
        r"\??\UNC\server\share\skills\shared",
        r"\\server\share\skills\shared",
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_trusted_unc_reparse_target_uses_relative_open(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет открытие внутренней цели UNC от корневого дескриптора."""
    windows_io = importlib.import_module("skillhub.registry._windows_resolver")
    root = Handle(
        token=1,
        final_path=Path(r"\\server\share\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )
    parent = Handle(
        token=2,
        final_path=Path(r"\\server\share\skills\skill"),
        identity=(1, 2),
        is_directory=True,
        is_regular=False,
    )
    child = Handle(
        token=3,
        final_path=Path(r"\\server\share\skills\shared"),
        identity=(1, 3),
        is_directory=True,
        is_regular=False,
    )
    opened: list[tuple[Handle, str]] = []

    monkeypatch.setattr(
        windows_io,
        "_probe_relative",
        lambda open_parent, name: (
            b"reparse" if open_parent is parent and name == "link" else None
        ),
    )
    monkeypatch.setattr(
        windows_io,
        "parse_reparse_target",
        lambda _data: (target, False),
    )

    def relative_open(open_parent: Handle, name: str, _access: int) -> int:
        opened.append((open_parent, name))
        return child.token

    monkeypatch.setattr(windows_io, "_open_relative", relative_open)
    monkeypatch.setattr(
        windows_io,
        "_reparse_data_from_open_token",
        lambda _token: None,
    )
    monkeypatch.setattr(windows_io, "_make_handle", lambda _token: child)

    assert windows_io.open_child(root, parent, "link") is child
    assert opened == [(root, "shared")]


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_resolution_budget_is_cumulative_and_closes_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет общий предел цепочки и очистку промежуточных дескрипторов."""
    windows_io = importlib.import_module("skillhub.registry._windows_resolver")
    root = Handle(
        token=1,
        final_path=Path(r"C:\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )
    opened: list[int] = []
    closed: list[int] = []
    active_peaks: list[int] = []
    expansions = 0

    def plain_probe(_parent: Handle, name: str) -> bytes | None:
        return b"link" if name.startswith("link-") else None

    def open_token(_parent: Handle, _name: str, _access: int) -> int:
        token = len(opened) + 10
        opened.append(token)
        return token

    def make_directory(token: int) -> Handle:
        active_peaks.append(len(opened) - len(closed))
        return Handle(
            token=token,
            final_path=root.final_path / f"opened-{token}",
            identity=(1, token),
            is_directory=True,
            is_regular=False,
        )

    def target_queue(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        nonlocal expansions
        expansions += 1
        if expansions == 1:
            return (*tuple(f"source-{index}" for index in range(129)), "link-2")
        return tuple(f"target-{index}" for index in range(124))

    monkeypatch.setattr(windows_io, "_probe_relative", plain_probe)
    monkeypatch.setattr(windows_io, "_open_relative", open_token)
    monkeypatch.setattr(
        windows_io,
        "_reparse_data_from_open_token",
        lambda _token: None,
    )
    monkeypatch.setattr(windows_io, "_make_handle", make_directory)
    monkeypatch.setattr(
        windows_io,
        "parse_reparse_target",
        lambda _data: (r"C:\skills\target", False),
    )
    monkeypatch.setattr(
        windows_io,
        "target_components",
        target_queue,
    )
    monkeypatch.setattr(windows_io, "close", lambda handle: closed.append(handle.token))

    with pytest.raises(UnsafePathError):
        windows_io.open_child(root, root, "link-1")

    assert expansions == 2
    assert len(opened) == 129
    assert closed == opened
    assert max(active_peaks) <= 2


def test_resolution_budget_counts_components_and_links_together() -> None:
    """Проверяет единый platform-neutral бюджет разрешения пути."""
    budget = ResolutionBudget()

    for _ in range(MAX_RESOLUTION_STEPS - 1):
        budget.visit_component()
    budget.follow_link()
    with pytest.raises(UnsafePathError):
        budget.visit_component()


def test_resolution_budget_allows_exact_link_hop_limit_only() -> None:
    """Проверяет включительную границу переходов по ссылкам."""
    budget = ResolutionBudget()

    for _ in range(MAX_LINK_HOPS):
        budget.follow_link()

    assert budget.link_hops == MAX_LINK_HOPS
    with pytest.raises(UnsafePathError):
        budget.follow_link()


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_reparse_probe_is_attribute_only_and_nofollow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет атрибутный режим первого открытия без следования цели."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    open_probe = cast("Callable[[str], int]", vars(windows_io)["_open_probe"])
    observed_access = -1
    observed_flags = 0

    def fake_create_file(
        _path: str,
        access: int,
        _share: int,
        _security: object,
        _creation: int,
        flags: int,
        _template: object,
    ) -> int:
        nonlocal observed_access, observed_flags
        observed_access = access
        observed_flags = flags
        return 123

    monkeypatch.setattr(windows_io, "_CreateFileW", fake_create_file)

    assert open_probe(r"\\?\C:\root\entry") == 123
    assert observed_access == vars(windows_io)["_FILE_READ_ATTRIBUTES"]
    assert observed_flags & vars(windows_io)["_FILE_FLAG_OPEN_REPARSE_POINT"]


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_child_reparse_probe_is_attribute_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет безопасный режим проверки дочерней точки повторного анализа."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    probe_relative = cast(
        "Callable[[Handle, str], bytes | None]",
        vars(windows_io)["_probe_relative"],
    )
    observed_access = -1
    closed: list[int] = []

    def capture_open(_parent: Handle, _name: str, access: int) -> int:
        nonlocal observed_access
        observed_access = access
        return 321

    def no_reparse_data(_token: int) -> None:
        return None

    def record_close(token: int) -> bool:
        closed.append(token)
        return True

    monkeypatch.setattr(windows_io, "_open_relative", capture_open)
    monkeypatch.setattr(
        windows_io,
        "_reparse_data_from_open_token",
        no_reparse_data,
    )
    monkeypatch.setattr(windows_io, "_CloseHandle", record_close)
    parent = Handle(
        token=123,
        final_path=Path(r"C:\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )

    assert probe_relative(parent, "child") is None
    assert observed_access == vars(windows_io)["_FILE_READ_ATTRIBUTES"]
    assert closed == [321]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        struct.pack("<IHH", 0xDEADBEEF, 0, 0),
        struct.pack("<IHH", 0xA0000003, 64, 0),
        struct.pack("<IHH", 0xA0000003, 0, 0),
        struct.pack("<IHH", 0xA000000C, 0, 0),
        struct.pack("<IHHHHHH", 0xA0000003, 9, 0, 0, 1, 0, 0) + b"x",
        struct.pack("<IHHHHHH", 0xA0000003, 10, 0, 0, 4, 0, 0) + b"xx",
        struct.pack("<IHHHHHH", 0xA0000003, 17, 0, 1, 8, 0, 0)
        + b"xC\x00:\x00\\\x00x\x00",
        struct.pack("<IHHHHHHI", 0xA000000C, 13, 0, 0, 1, 0, 0, 0) + b"x",
        struct.pack("<IHHHHHHI", 0xA000000C, 15, 0, 1, 2, 0, 0, 0) + b"xx\x00",
        struct.pack("<IHHHHHH", 0xA0000003, 10, 0, 0, 2, 0, 0) + b"\x00\xd8",
        struct.pack("<IHHHHHH", 0xA0000003, 8, 0, 0, 0, 0, 0),
    ],
)
def test_windows_reparse_parser_rejects_malformed_buffers(payload: bytes) -> None:
    """Проверяет безопасный отказ для повреждённых буферов ссылок."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    parse_target = cast(
        "Callable[[bytes], tuple[str, bool]]",
        vars(windows_io)["parse_reparse_target"],
    )

    with pytest.raises(UnsafePathError):
        parse_target(payload)


def test_windows_resolver_has_one_working_entry() -> None:
    """Проверяет единый вход разрешения пути без прежнего мёртвого обхода."""
    windows_io = importlib.import_module("skillhub.registry._windows_io")
    windows_resolver = importlib.import_module("skillhub.registry._windows_resolver")

    assert windows_io.open_child is windows_resolver.open_child
    assert "_open_components" not in vars(windows_resolver)


@pytest.mark.parametrize(
    "payload",
    [
        struct.pack("<IHHHHHH", 0xA0000003, 10, 0, 0, 2, 1, 0) + b"x\x00",
        struct.pack("<IHHHHHH", 0xA0000003, 10, 0, 0, 2, 0, 1) + b"x\x00",
        struct.pack("<IHHHHHH", 0xA0000003, 10, 0, 0, 2, 2, 2) + b"x\x00",
        struct.pack("<IHHHHHHI", 0xA000000C, 14, 0, 0, 2, 1, 0, 0) + b"x\x00",
        struct.pack("<IHHHHHHI", 0xA000000C, 14, 0, 0, 2, 0, 1, 0) + b"x\x00",
        struct.pack("<IHHHHHHI", 0xA000000C, 14, 0, 0, 2, 2, 2, 0) + b"x\x00",
    ],
)
def test_windows_reparse_parser_rejects_invalid_print_name_range(
    payload: bytes,
) -> None:
    """Проверяет границы и выравнивание поля PrintName до декодирования."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    parse_target = cast(
        "Callable[[bytes], tuple[str, bool]]",
        vars(windows_io)["parse_reparse_target"],
    )

    with pytest.raises(UnsafePathError):
        parse_target(payload)


@pytest.mark.parametrize(
    "target",
    [
        r"\??\UNC\server\share\skills\shared",
        r"\\server\share\skills\shared",
    ],
)
def test_windows_internal_unc_reparse_target_is_rooted(
    target: str,
) -> None:
    """Проверяет компоненты внутренней цели для доверенного корня UNC."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    target_components = cast(
        "Callable[..., tuple[str, ...]]",
        vars(windows_io)["target_components"],
    )
    root = Path(r"\\server\share\skills")

    assert target_components(
        target,
        relative=False,
        parent=root / "skill",
        root=root,
        remaining=("SKILL.md",),
    ) == ("shared", "SKILL.md")


def test_windows_reparse_target_normalization_is_rooted_and_bounded() -> None:
    """Проверяет нормализацию внутренних целей и границы разрешения."""
    windows_io = importlib.import_module("skillhub.registry._windows_reparse")
    target_components = cast(
        "Callable[..., tuple[str, ...]]",
        vars(windows_io)["target_components"],
    )
    root = Path(r"C:\skills")
    parent = root / "skill"

    assert target_components(
        r"..\shared",
        relative=True,
        parent=parent,
        root=root,
        remaining=(),
    ) == ("shared",)
    assert (
        target_components(
            r"\??\C:\skills",
            relative=False,
            parent=parent,
            root=root,
            remaining=(),
        )
        == ()
    )
    unsafe_targets = (
        (r"\??\UNC\server\share\private", False),
        (r"\??\D:\outside", False),
        ("missing-drive", False),
        (r"\??\C:\skills\shared", True),
    )
    for target, relative in unsafe_targets:
        with pytest.raises(UnsafePathError):
            target_components(
                target,
                relative=relative,
                parent=parent,
                root=root,
                remaining=(),
            )


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_native_relative_open_requests_nofollow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет запрет следования при относительном открытии потомка."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    open_relative = cast(
        "Callable[[Handle, str, int], int]",
        vars(windows_io)["_open_relative"],
    )
    observed_access = 0
    observed_options = 0

    def fake_nt_create(*args: object) -> int:
        nonlocal observed_access, observed_options
        observed_access = cast("int", args[1])
        observed_options = cast("int", args[8])
        return 0

    monkeypatch.setattr(windows_io, "_NtCreateFile", fake_nt_create)
    parent = Handle(
        token=123,
        final_path=Path(r"C:\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )

    with pytest.raises(OSError, match=r"^$"):
        open_relative(
            parent,
            "child",
            cast("int", vars(windows_io)["_FILE_READ_ATTRIBUTES"]),
        )

    assert observed_access & vars(windows_io)["_FILE_READ_ATTRIBUTES"]
    assert observed_options & vars(windows_io)["_FILE_OPEN_REPARSE_POINT"]


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_native_error_paths_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет безопасный отказ при ошибках нативных операций."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    denied_message = "denied"

    def denied() -> None:
        raise OSError(denied_message)

    def invalid_create(*_args: object) -> int:
        return cast("int", vars(windows_io)["_INVALID_HANDLE_VALUE"])

    def failed_call(*_args: object) -> bool:
        return False

    monkeypatch.setattr(windows_io, "_raise_last_error", denied)
    monkeypatch.setattr(windows_io, "_CreateFileW", invalid_create)
    open_raw = cast(
        "Callable[[str, int], int]",
        vars(windows_io)["_open_raw"],
    )
    with pytest.raises(OSError, match="denied"):
        open_raw("unused", 0)

    handle = Handle(
        token=123,
        final_path=Path(r"C:\skills\file"),
        identity=(1, 2),
        is_directory=False,
        is_regular=True,
    )
    monkeypatch.setattr(windows_io, "_ReadFile", failed_call)
    with pytest.raises(OSError, match="denied"):
        cast("Callable[[Handle, int], bytes]", vars(windows_io)["read"])(handle, 1)

    monkeypatch.setattr(windows_io, "_GetFileInformationByHandle", failed_call)
    with pytest.raises(OSError, match="denied"):
        cast("Callable[[int], object]", vars(windows_io)["_file_information"])(123)

    monkeypatch.setattr(windows_io, "_DeviceIoControl", failed_call)
    with pytest.raises(OSError, match="denied"):
        cast("Callable[[int], bytes]", vars(windows_io)["_read_reparse_data"])(123)


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_failed_metadata_closes_open_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет освобождение корневого и дочернего дескриптора при отказе."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    closed: list[int] = []
    denied_message = "denied"

    def open_token(_path: str) -> int:
        return 123

    def reject_reparse(_token: int) -> None:
        raise UnsafePathError

    def denied_information(_token: int) -> object:
        raise OSError(denied_message)

    def record_close(token: int) -> bool:
        closed.append(token)
        return True

    monkeypatch.setattr(windows_io, "_open_content", open_token)
    monkeypatch.setattr(windows_io, "_assert_not_reparse", reject_reparse)
    monkeypatch.setattr(windows_io, "_CloseHandle", record_close)
    open_plain_root = cast(
        "Callable[[str], Handle]",
        vars(windows_io)["_open_plain_root"],
    )
    with pytest.raises(UnsafePathError):
        open_plain_root("unused")
    assert closed == [123]

    closed.clear()
    monkeypatch.setattr(windows_io, "_file_information", denied_information)
    make_handle = cast("Callable[[int], Handle]", vars(windows_io)["_make_handle"])
    with pytest.raises(OSError, match="denied"):
        make_handle(456)
    assert closed == [456]


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_component_resolution_closes_raced_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет очистку при гонке ссылки и неверном промежуточном типе."""
    windows_io = importlib.import_module("skillhub.registry._windows_resolver")
    root = Handle(
        token=1,
        final_path=Path(r"C:\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )
    regular = Handle(
        token=2,
        final_path=Path(r"C:\skills\file"),
        identity=(1, 2),
        is_directory=False,
        is_regular=True,
    )
    closed: list[int] = []
    denied_message = "denied"

    def open_token(_parent: Handle, _name: str, _access: int) -> int:
        return 123

    def denied_reparse(_token: int) -> bytes | None:
        raise OSError(denied_message)

    def record_close(token: int) -> bool:
        closed.append(token)
        return True

    monkeypatch.setattr(windows_io, "_probe_relative", lambda *_args: None)
    monkeypatch.setattr(windows_io, "_open_relative", open_token)
    monkeypatch.setattr(windows_io, "_reparse_data_from_open_token", denied_reparse)
    monkeypatch.setattr(windows_io, "_CloseHandle", record_close)
    with pytest.raises(OSError, match=denied_message):
        windows_io.open_child(root, root, "entry")
    assert closed == [123]

    closed.clear()

    monkeypatch.setattr(
        windows_io,
        "_reparse_data_from_open_token",
        lambda _token: None,
    )
    monkeypatch.setattr(
        windows_io,
        "_probe_relative",
        lambda _parent, name: b"reparse" if name == "link" else None,
    )
    monkeypatch.setattr(
        windows_io,
        "parse_reparse_target",
        lambda _data: (r"C:\skills\entry\child", False),
    )
    monkeypatch.setattr(windows_io, "_make_handle", lambda _token: regular)
    monkeypatch.setattr(
        windows_io,
        "close",
        lambda handle: closed.append(handle.token),
    )
    with pytest.raises(UnsafePathError):
        windows_io.open_child(root, root, "link")
    assert closed == [regular.token]


@pytest.mark.skipif(os.name != "nt", reason="Проверка относится к Win32")
def test_windows_path_api_boundaries_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет ошибки конечного пути, перечисления и длинные формы пути."""
    windows_io = importlib.import_module("skillhub.registry._windows_handles")
    windows_directory = importlib.import_module("skillhub.registry._windows_directory")
    raise_last_error = cast(
        "Callable[[], None]",
        vars(windows_io)["_raise_last_error"],
    )
    ctypes.set_last_error(5)
    with pytest.raises(PermissionError):
        raise_last_error()

    denied_message = "denied"

    def denied() -> None:
        raise OSError(denied_message)

    final_path = cast("Callable[[int], Path]", vars(windows_io)["_final_path"])
    monkeypatch.setattr(windows_io, "_raise_last_error", denied)

    def missing_path(*_args: object) -> int:
        return 0

    monkeypatch.setattr(windows_io, "_GetFinalPathNameByHandleW", missing_path)
    with pytest.raises(OSError, match=denied_message):
        final_path(123)

    calls = 0

    def oversized_path(*_args: object) -> int:
        nonlocal calls
        calls += 1
        return 4 if calls == 1 else 5

    monkeypatch.setattr(windows_io, "_GetFinalPathNameByHandleW", oversized_path)
    with pytest.raises(OSError, match=denied_message):
        final_path(123)

    extended_path = cast(
        "Callable[[Path], str]",
        vars(windows_io)["_extended_path"],
    )
    assert extended_path(Path(r"\\?\C:\skills")) == r"\\?\C:\skills"
    assert extended_path(Path(r"\\server\share")) == "\\\\?\\UNC\\server\\share\\"

    def failed_listing(*_args: object) -> bool:
        return False

    monkeypatch.setattr(
        windows_directory,
        "_GetFileInformationByHandleEx",
        failed_listing,
    )
    fill_buffer = cast(
        "Callable[[Handle, int, object], bool]",
        vars(windows_directory)["_fill_directory_buffer"],
    )
    directory = Handle(
        token=123,
        final_path=Path(r"C:\skills"),
        identity=(1, 1),
        is_directory=True,
        is_regular=False,
    )
    buffer = ctypes.create_string_buffer(64)
    monkeypatch.setattr(windows_directory.ctypes, "get_last_error", lambda: 18)
    assert fill_buffer(directory, 10, buffer) is False
    monkeypatch.setattr(windows_directory.ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(windows_directory, "_raise_last_error", denied)
    with pytest.raises(OSError, match=denied_message):
        fill_buffer(directory, 10, buffer)


def test_opened_special_file_handle_is_rejected_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет запрет чтения special file независимо от возможностей ОС."""
    root = tmp_path / "skills"
    directory = root / "special"
    _write_skill(directory, name="special")
    backend = _platform_adapter()
    original_open_child = backend.open_child
    read_attempted = False

    def special_file_open(
        root_handle: Handle,
        parent: Handle,
        name: str,
    ) -> Handle:
        if name != "SKILL.md":
            return original_open_child(root_handle, parent, name)
        return Handle(
            token=-1,
            final_path=(directory / "SKILL.md").resolve(),
            identity=(999, 3),
            is_directory=False,
            is_regular=False,
        )

    def forbidden_read(_handle: Handle, _size: int) -> bytes:
        nonlocal read_attempted
        read_attempted = True
        return b"must-not-be-read"

    monkeypatch.setattr(backend, "open_child", special_file_open)
    monkeypatch.setattr(backend, "read", forbidden_read)

    report = SkillRegistry(root).load()

    assert read_attempted is False
    assert report.skills == ()
    assert report.issues == (LoadIssue(path="special", reason="missing_file"),)


@pytest.mark.skipif(os.name != "posix", reason="FIFO доступен только на POSIX")
def test_posix_special_file_is_opened_nonblocking_and_not_read(
    tmp_path: Path,
) -> None:
    """Проверяет неблокирующий отказ для FIFO вместо чтения."""
    directory = tmp_path / "fifo-skill"
    directory.mkdir()
    fifo = directory / "SKILL.md"
    make_fifo = cast("Callable[[Path], None]", vars(os)["mkfifo"])
    make_fifo(fifo)
    posix_io = importlib.import_module("skillhub.registry._posix_io")
    open_flags = cast("int", vars(posix_io)["OPEN_FLAGS"])

    report = SkillRegistry(tmp_path).load()

    assert open_flags & cast("int", vars(os)["O_NONBLOCK"])
    assert open_flags & cast("int", vars(os)["O_NOFOLLOW"])
    assert report.skills == ()
    assert _issue_reasons(report) == ("missing_file",)
