"""Проверяет снимок, перезагрузку и поиск реестра."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from inspect import signature
from pathlib import Path
from shutil import rmtree
from threading import Barrier, Event, Lock, Thread
from typing import cast

import pytest

import skillhub.registry._registry as registry_module
from skillhub.registry import LoadReport, Skill, SkillRegistry


def _skill(name: str = "alpha") -> Skill:
    """Создаёт неизменяемый скилл для проверки фасада.

    Args:
        name: каноническое имя скилла.

    Returns:
        Тестовые данные скилла.
    """
    return Skill(
        name=name,
        caption=f"Режим {name}",
        description=f"Описание {name}",
        body=f"Тело {name}",
        has_files=False,
    )


def _write_skill(
    root: Path,
    name: str,
    caption: str | None = None,
    *,
    directory_name: str | None = None,
) -> None:
    """Записывает валидный каталог скилла.

    Args:
        root: корневой каталог реестра.
        name: каноническое имя скилла.
        caption: подпись скилла или значение по умолчанию.
        directory_name: отличающееся имя каталога для проверки порядка.
    """
    directory = root / (directory_name or name)
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"caption: {caption or f'Режим {name}'}\n"
        f"description: Описание {name}\n"
        "---\n"
        f"Тело {name}",
        encoding="utf-8",
    )


class _ObservedLock:
    """Отмечает попытки входа перед делегированием обычной блокировке."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._guard = Lock()
        self._attempts = 0
        self.second_attempt = Event()

    def __enter__(self) -> None:
        with self._guard:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempt.set()
        self._lock.acquire()

    def __exit__(self, *_args: object) -> None:
        self._lock.release()


class _PublicationObservedRegistry(SkillRegistry):
    """Считает замены поколения, не меняя публичное поведение реестра."""

    __slots__ = ("publication_count",)

    def __init__(self, root: Path) -> None:
        self.publication_count = 0
        super().__init__(root)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_generation" and hasattr(self, "_generation"):
            object.__setattr__(
                self,
                "publication_count",
                self.publication_count + 1,
            )
        object.__setattr__(self, name, value)


class _OverriddenLoadRegistry(SkillRegistry):
    """Возвращает заведомо неканонический отчёт только из публичного load()."""

    __slots__ = ()

    def load(self) -> LoadReport:
        """Возвращает отчёт с повторным именем в обход файловой загрузки.

        Returns:
            Заведомо неканонический отчёт.
        """
        return LoadReport(
            skills=(
                _skill("spoofed"),
                Skill(
                    name="spoofed",
                    caption="Подмена",
                    description="Непроверенное описание",
                    body="Непроверенное тело",
                    has_files=False,
                ),
            ),
            issues=(),
        )


def test_registry_starts_with_immutable_empty_snapshot(tmp_path: Path) -> None:
    """Проверяет явный пустой снимок без чтения файлов при создании."""
    registry = SkillRegistry(tmp_path)

    snapshot = registry.snapshot

    assert snapshot == {}
    with pytest.raises(TypeError):
        cast("dict[str, Skill]", snapshot)["alpha"] = _skill()


def test_initial_capture_matches_empty_snapshot_report_and_search(
    tmp_path: Path,
) -> None:
    """Связывает исходные пустые данные в одном неизменяемом поколении."""
    registry = SkillRegistry(tmp_path)

    capture = registry.capture()

    assert capture.snapshot is registry.snapshot
    assert capture.snapshot == {}
    assert capture.report == LoadReport(skills=(), issues=())
    assert capture.search("") == ()


def test_reload_returns_report_from_one_published_capture(tmp_path: Path) -> None:
    """Связывает отчёт, снимок и поиск одной опубликованной загрузки."""
    _write_skill(tmp_path, "zeta", directory_name="alpha-directory")
    _write_skill(tmp_path, "alpha", directory_name="zeta-directory")
    registry = SkillRegistry(tmp_path)

    report = registry.reload()
    capture = registry.capture()

    assert capture.report is report
    assert capture.snapshot is registry.snapshot
    assert tuple(capture.snapshot) == ("alpha", "zeta")
    assert capture.search("") == report.search("")


def test_registry_constructor_rejects_loader_injection(tmp_path: Path) -> None:
    """Проверяет отклонение вызываемого объекта в обход файловой загрузки."""
    with pytest.raises(TypeError, match="_loader"):
        SkillRegistry(  # type: ignore[call-arg]
            tmp_path,
            _loader=lambda _root: LoadReport(skills=(_skill("spoofed"),), issues=()),
        )


def test_registry_constructor_exposes_only_root() -> None:
    """Проверяет отсутствие альтернативного источника в публичной сигнатуре."""
    assert tuple(signature(SkillRegistry).parameters) == ("root",)


def test_reload_replaces_snapshot_and_preserves_old_reference(tmp_path: Path) -> None:
    """Проверяет замену целого снимка с сохранением старой ссылки."""
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)
    initial = registry.snapshot

    report = registry.reload()
    loaded = registry.snapshot

    assert report.skills == (_skill(),)
    assert tuple(loaded) == ("alpha",)
    assert loaded is not initial
    assert initial == {}

    _write_skill(tmp_path, "bravo")
    registry.reload()

    assert tuple(registry.snapshot) == ("alpha", "bravo")
    assert tuple(loaded) == ("alpha",)


def test_load_returns_report_without_publishing_snapshot(tmp_path: Path) -> None:
    """Проверяет отсутствие публикации при отдельной загрузке."""
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)

    report = registry.load()

    assert tuple(skill.name for skill in report.skills) == ("alpha",)
    assert registry.snapshot == {}


def test_public_load_never_publishes_arbitrary_report(tmp_path: Path) -> None:
    """Проверяет, что публичный load() не публикует произвольный отчёт."""
    registry = _OverriddenLoadRegistry(tmp_path)

    report = registry.load()

    assert tuple(skill.name for skill in report.skills) == ("spoofed", "spoofed")
    assert registry.snapshot == {}


def test_reload_ignores_overridden_public_load(tmp_path: Path) -> None:
    """Проверяет загрузку снимка только через доверенную приватную границу."""
    _write_skill(tmp_path, "alpha")
    registry = _OverriddenLoadRegistry(tmp_path)

    report = registry.reload()

    assert report.skills == (_skill(),)
    assert tuple(registry.snapshot) == ("alpha",)
    assert "spoofed" not in registry.snapshot


def test_reload_ignores_replaced_public_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что reload() не вызывает заменяемый публичный метод load()."""
    _write_skill(tmp_path, "alpha")
    malicious_report = _OverriddenLoadRegistry(tmp_path).load()
    monkeypatch.setattr(SkillRegistry, "load", lambda _registry: malicious_report)
    registry = SkillRegistry(tmp_path)

    report = registry.reload()

    assert report.skills == (_skill(),)
    assert tuple(registry.snapshot) == ("alpha",)
    assert "spoofed" not in registry.snapshot


def test_changed_tree_is_published_only_by_reload_and_keeps_old_values(
    tmp_path: Path,
) -> None:
    """Проверяет непубликующий load после уже заполненного снимка."""
    _write_skill(tmp_path, "alpha", "Старая подпись")
    registry = SkillRegistry(tmp_path)
    registry.reload()
    old_snapshot = registry.snapshot
    old_skill = old_snapshot["alpha"]

    rmtree(tmp_path / "alpha")
    _write_skill(tmp_path, "alpha", "Новая подпись")

    loaded = registry.load()

    assert loaded.skills[0].caption == "Новая подпись"
    assert registry.snapshot is old_snapshot
    assert old_skill.caption == "Старая подпись"

    reloaded = registry.reload()

    assert reloaded == loaded
    assert registry.snapshot is not old_snapshot
    assert registry.snapshot["alpha"].caption == "Новая подпись"
    assert old_snapshot["alpha"] is old_skill
    assert old_skill.caption == "Старая подпись"


def test_each_reload_publishes_exactly_once_and_other_readers_never_publish(
    tmp_path: Path,
) -> None:
    """Проверяет единственную замену снимка на один вызов reload."""
    _write_skill(tmp_path, "alpha")
    registry = _PublicationObservedRegistry(tmp_path)
    initial = registry.snapshot

    registry.load()
    registry.search("")

    assert registry.publication_count == 0
    assert registry.snapshot is initial

    registry.reload()

    assert registry.publication_count == 1
    published = registry.snapshot
    assert published is not initial

    registry.load()
    registry.search("ALPHA")

    assert registry.publication_count == 1
    assert registry.snapshot is published


def test_published_skill_rejects_external_mutation(tmp_path: Path) -> None:
    """Проверяет неизменяемость скилла в опубликованном снимке."""
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)
    registry.reload()
    skill_attribute = "caption"

    with pytest.raises(FrozenInstanceError):
        setattr(registry.snapshot["alpha"], skill_attribute, "Изменено")


def test_search_filters_name_and_caption_with_casefold(tmp_path: Path) -> None:
    """Проверяет регистронезависимый поиск без изменения снимка."""
    _write_skill(tmp_path, "zeta", "Другой режим")
    _write_skill(tmp_path, "alpha", "Первый режим")
    _write_skill(tmp_path, "beta", "Straße")
    registry = SkillRegistry(tmp_path)
    registry.reload()
    snapshot = registry.snapshot

    by_name = registry.search("ALP")
    by_caption = registry.search("STRASSE")

    assert tuple(skill.name for skill in by_name) == ("alpha",)
    assert tuple(skill.name for skill in by_caption) == ("beta",)
    assert registry.snapshot is snapshot


def test_search_with_multiple_caption_matches_keeps_canonical_name_order(
    tmp_path: Path,
) -> None:
    """Проверяет канонический порядок всех casefold-совпадений подписи."""
    _write_skill(tmp_path, "zeta", "Straße A", directory_name="alpha-directory")
    _write_skill(tmp_path, "alpha", "Straße Z", directory_name="zeta-directory")
    _write_skill(tmp_path, "bravo", "Straße M", directory_name="middle-directory")
    registry = SkillRegistry(tmp_path)
    registry.reload()

    result = registry.search("STRASSE")

    assert tuple(skill.name for skill in result) == ("alpha", "bravo", "zeta")


def test_search_empty_query_returns_all_and_no_match_returns_empty(
    tmp_path: Path,
) -> None:
    """Проверяет явную семантику пустого и безрезультатного поиска."""
    _write_skill(tmp_path, "zeta", directory_name="alpha-directory")
    _write_skill(tmp_path, "alpha", directory_name="zeta-directory")
    registry = SkillRegistry(tmp_path)
    registry.reload()

    all_skills = registry.search("")
    missing = registry.search("missing")

    assert isinstance(all_skills, tuple)
    assert tuple(skill.name for skill in all_skills) == ("alpha", "zeta")
    assert missing == ()


def test_reload_publishes_five_valid_skills_and_reports_broken_sixth(
    tmp_path: Path,
) -> None:
    """Проверяет публикацию валидных соседей и производные счётчики."""
    names = ("echo", "delta", "charlie", "bravo", "alpha")
    for name in names:
        _write_skill(tmp_path, name)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text(
        "---\nname: broken\ncaption: Broken\n---\nBody",
        encoding="utf-8",
    )
    registry = SkillRegistry(tmp_path)

    report = registry.reload()

    assert tuple(registry.snapshot) == tuple(sorted(names))
    assert report.loaded == 5
    assert report.skipped == 1
    assert report.issues[0].reason == "missing_description"


def test_simultaneous_reloads_are_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет единственную активную загрузку при двух reload()."""
    observed_lock = _ObservedLock()
    monkeypatch.setattr(registry_module, "Lock", lambda: observed_lock)
    state_guard = Lock()
    first_entered = Event()
    second_entered = Event()
    release_first = Event()
    calls = 0
    active = 0
    max_active = 0

    def loader(_root: Path) -> LoadReport:
        nonlocal active, calls, max_active
        with state_guard:
            calls += 1
            call_number = calls
            active += 1
            max_active = max(max_active, active)
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        else:
            second_entered.set()
        with state_guard:
            active -= 1
        return LoadReport(skills=(_skill(f"skill-{call_number}"),), issues=())

    monkeypatch.setattr(registry_module, "_load_registry", loader)
    registry = SkillRegistry(tmp_path)
    first = Thread(target=registry.reload)
    second = Thread(target=registry.reload)

    first.start()
    assert first_entered.wait(timeout=5)
    second.start()
    assert observed_lock.second_attempt.wait(timeout=5)

    assert max_active == 1
    assert second_entered.is_set() is False

    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert max_active == 1


def test_four_readers_observe_only_whole_snapshots_during_reloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет чтение старого или нового снимка без блокировки."""
    old_report = LoadReport(
        skills=(_skill("alpha"), _skill("bravo")),
        issues=(),
    )
    new_report = LoadReport(
        skills=(_skill("charlie"), _skill("delta")),
        issues=(),
    )
    steps = [
        (
            new_report if index % 2 == 0 else old_report,
            Event(),
            Event(),
        )
        for index in range(6)
    ]
    loader_guard = Lock()
    calls = 0

    def loader(_root: Path) -> LoadReport:
        nonlocal calls
        with loader_guard:
            call_index = calls
            calls += 1
        if call_index == 0:
            return old_report
        report, entered, release = steps[call_index - 1]
        entered.set()
        assert release.wait(timeout=5)
        return report

    monkeypatch.setattr(registry_module, "_load_registry", loader)
    registry = SkillRegistry(tmp_path)
    registry.reload()
    reader_barrier = Barrier(5)

    def read_snapshots() -> tuple[tuple[str, ...], ...]:
        observations: list[tuple[str, ...]] = []
        for _ in range(len(steps) * 2):
            reader_barrier.wait(timeout=5)
            capture = registry.capture()
            keys = tuple(capture.snapshot)
            assert tuple(skill.name for skill in capture.search("")) == keys
            assert tuple(skill.name for skill in capture.report.search("")) == keys
            observations.append(keys)
            reader_barrier.wait(timeout=5)
        return tuple(observations)

    with ThreadPoolExecutor(max_workers=4) as executor:
        readers = [executor.submit(read_snapshots) for _ in range(4)]
        for _report, entered, release in steps:
            reload_thread = Thread(target=registry.reload)
            reload_thread.start()
            assert entered.wait(timeout=5)

            reader_barrier.wait(timeout=5)
            reader_barrier.wait(timeout=5)

            release.set()
            reload_thread.join(timeout=5)
            assert reload_thread.is_alive() is False

            reader_barrier.wait(timeout=5)
            reader_barrier.wait(timeout=5)

        observations = tuple(
            observation
            for reader in readers
            for observation in reader.result(timeout=5)
        )

    allowed = {("alpha", "bravo"), ("charlie", "delta")}
    assert set(observations) == allowed


def test_missing_and_empty_root_reload_publish_predictable_empty_snapshot(
    tmp_path: Path,
) -> None:
    """Проверяет очистку снимка для отсутствующего и пустого корня."""
    root = tmp_path / "skills"
    _write_skill(root, "alpha")
    registry = SkillRegistry(root)
    registry.reload()
    rmtree(root)

    missing = registry.reload()

    assert registry.snapshot == {}
    assert missing.loaded == 0
    assert missing.skipped == 1
    assert missing.issues[0].reason == "root_missing"

    root.mkdir()
    empty = registry.reload()

    assert registry.snapshot == {}
    assert empty == LoadReport(skills=(), issues=())
    assert empty.loaded == 0
    assert empty.skipped == 0


def test_repeated_unchanged_reload_preserves_values_and_order(
    tmp_path: Path,
) -> None:
    """Проверяет идемпотентность значений при повторной перезагрузке."""
    _write_skill(tmp_path, "zeta")
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)

    first_report = registry.reload()
    first_snapshot = registry.snapshot
    second_report = registry.reload()
    second_snapshot = registry.snapshot

    assert first_report == second_report
    assert first_report.loaded == second_report.loaded == 2
    assert first_report.skipped == second_report.skipped == 0
    assert tuple(first_snapshot.items()) == tuple(second_snapshot.items())
    assert tuple(second_snapshot) == ("alpha", "zeta")
    assert second_snapshot is not first_snapshot
