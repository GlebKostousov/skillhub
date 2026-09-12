"""Управляет ограниченной загрузкой через разбор документа и файловую границу."""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from types import MappingProxyType

import structlog

import skillhub.registry._filesystem as filesystem
from skillhub.registry._document import parse_document
from skillhub.registry._errors import InvalidSkillError, RegistryLimitError, reject
from skillhub.registry._limits import MAX_TOTAL_SKILL_BYTES
from skillhub.registry._models import LoadIssue, LoadReport, Skill, _search_skills

_SAFE_PATH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(slots=True)
class _LoadState:
    """Хранит ограниченное состояние одной последовательной загрузки."""

    skills: list[Skill]
    issues: list[LoadIssue]
    names: set[str]
    remaining_bytes: int


@dataclass(frozen=True, slots=True)
class RegistryCapture:
    """Связывает отчёт и снимок одного опубликованного поколения.

    Attributes:
        report: неизменяемый итог той же загрузки.
        snapshot: неизменяемое отображение скиллов той же загрузки.
    """

    report: LoadReport
    snapshot: Mapping[str, Skill]

    def search(self, query: str) -> tuple[Skill, ...]:
        """Ищет скиллы внутри захваченного поколения.

        Args:
            query: искомая часть имени или подписи.

        Returns:
            Подходящие скиллы в порядке канонического имени.
        """
        return _search_skills(self.snapshot.values(), query)


class SkillRegistry:
    """Хранит одно опубликованное поколение проверенных скиллов.

    После создания поколение пусто. Метод load() читает дерево без публикации.
    Метод reload() сериализует полное чтение и публикует новый
    RegistryCapture одним присваиванием. Методы capture(), snapshot и search()
    не берут блокировку перезагрузки.
    """

    __slots__ = ("_generation", "_reload_lock", "_root")

    def __init__(self, root: Path) -> None:
        """Сохраняет корень последующих чтений.

        Args:
            root: корневой каталог скиллов.
        """
        self._root = root
        self._reload_lock = Lock()
        self._generation = _capture_from_report(LoadReport(skills=(), issues=()))

    @property
    def snapshot(self) -> Mapping[str, Skill]:
        """Возвращает без блокировки текущий неизменяемый снимок реестра.

        Returns:
            Ссылка на целый снимок, актуальный на момент чтения свойства.
        """
        return self._generation.snapshot

    def capture(self) -> RegistryCapture:
        """Возвращает отчёт и снимок одного текущего поколения.

        Returns:
            Неизменяемое поколение, опубликованное последним reload().
        """
        return self._generation

    def load(self) -> LoadReport:
        """Читает текущее дерево скиллов без публикации снимка.

        Returns:
            Независимый отчёт с неизменяемыми скиллами и причинами пропуска.
        """
        return _load_registry(self._root)

    def reload(self) -> LoadReport:
        """Публикует целое новое поколение после сериализованной загрузки.

        Returns:
            Отчёт, связанный с опубликованным снимком.
        """
        with self._reload_lock:
            report = _load_registry(self._root)
            generation = _capture_from_report(report)
            self._generation = generation
            return generation.report

    def search(self, query: str) -> tuple[Skill, ...]:
        """Ищет скиллы по имени или подписи в одном текущем снимке.

        Сравнение выполняется через Unicode casefold. Пустой запрос возвращает
        весь снимок; порядок результата задаётся каноническим именем скилла.

        Args:
            query: искомая часть имени или подписи.

        Returns:
            Подходящие скиллы из одного снимка.
        """
        generation = self._generation
        return generation.search(query)


def _load_registry(root: Path) -> LoadReport:
    """Создаёт полный отчёт через штатную файловую границу.

    Args:
        root: доверенно настроенный корневой каталог скиллов.

    Returns:
        Проверенные скиллы и безопасные причины пропуска.
    """
    try:
        resolved_root = root.resolve(strict=True)
        with filesystem.open_registry(resolved_root) as source:
            return _load_entries(source)
    except (OSError, RuntimeError, InvalidSkillError, RegistryLimitError) as exc:
        return _root_issue(_root_failure_reason(exc))


def _load_entries(source: filesystem.RegistryFilesystem) -> LoadReport:
    state = _LoadState(
        skills=[],
        issues=[],
        names=set(),
        remaining_bytes=MAX_TOTAL_SKILL_BYTES,
    )
    for entry_name in source.list_candidates():
        if not _load_next(source, entry_name, state):
            break
    return LoadReport(skills=tuple(state.skills), issues=tuple(state.issues))


def _canonical_skill_key(skill: Skill) -> str:
    return skill.name


def _capture_from_report(report: LoadReport) -> RegistryCapture:
    snapshot = MappingProxyType(
        {skill.name: skill for skill in sorted(report.skills, key=_canonical_skill_key)}
    )
    return RegistryCapture(report=report, snapshot=snapshot)


def _load_next(
    source: filesystem.RegistryFilesystem,
    entry_name: str,
    state: _LoadState,
) -> bool:
    safe_path = _safe_path_identifier(entry_name)
    try:
        skill = _load_entry(source, entry_name, state)
    except InvalidSkillError as exc:
        return _record_failure(state, safe_path, exc.reason)
    if skill is not None:
        state.skills.append(skill)
    return True


def _record_failure(state: _LoadState, path: str, reason: str) -> bool:
    if reason == "registry_limit_exceeded":
        state.issues.append(_report_issue(".", reason))
        return False
    state.issues.append(_report_issue(path, reason))
    return True


def _load_entry(
    source: filesystem.RegistryFilesystem,
    entry_name: str,
    state: _LoadState,
) -> Skill | None:
    with source.read_skill(entry_name, state.remaining_bytes) as document:
        if document is None:
            return None
        state.remaining_bytes -= len(document.payload)
        name, caption, description, body = parse_document(
            document.payload,
            entry_name,
        )
        _validate_unique_name(name, state)
        has_files = source.has_additional_files(document)
        state.names.add(name)
        return Skill(
            name=name,
            caption=caption,
            description=description,
            body=body,
            has_files=has_files,
        )


def _validate_unique_name(name: str, state: _LoadState) -> None:
    if name in state.names:
        reject("duplicate_name")


def _safe_path_identifier(name: str) -> str:
    if _SAFE_PATH_PATTERN.fullmatch(name) is not None and name not in {".", ".."}:
        return name
    digest = hashlib.sha256(name.encode("utf-8", errors="surrogatepass")).hexdigest()[
        :16
    ]
    return f"unsafe-{digest}"


def _root_failure_reason(
    exc: OSError | RuntimeError | InvalidSkillError | RegistryLimitError,
) -> str:
    if isinstance(exc, InvalidSkillError):
        return exc.reason
    if isinstance(exc, RegistryLimitError):
        return "registry_limit_exceeded"
    return _os_root_failure_reason(exc)


def _os_root_failure_reason(exc: OSError | RuntimeError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "root_missing"
    return "root_unreadable"


def _root_issue(reason: str) -> LoadReport:
    return LoadReport(skills=(), issues=(_report_issue(".", reason),))


def _report_issue(path: str, reason: str) -> LoadIssue:
    structlog.get_logger(__name__).warning(
        "registry.skill_skipped",
        error_code=reason,
        skill_path=path,
    )
    return LoadIssue(path=path, reason=reason)
