"""Проецирует поколение реестра в публичный каталог и одноразовый итог."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from pydantic import BaseModel, ConfigDict

from skillhub.registry import LoadIssue, LoadReport, RegistryCapture, Skill
from skillhub.web._issue_messages import issue_text
from skillhub.web.errors import QueryTooLongError, ReloadUnavailableError

MAX_SKILL_QUERY_LENGTH = 128
_MAX_OUTSTANDING_FLASHES = 8
_FLASH_TTL_SECONDS = 300.0
_FLASH_MARKER_BYTES = 32
_FLASH_MARKER_ATTEMPTS = 8
_FLASH_MARKER_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


class SkillSummary(BaseModel):
    """Описывает только публичные метаданные скилла."""

    model_config = ConfigDict(frozen=True)

    name: str
    caption: str
    description: str
    has_files: bool


class LoadIssueSummary(BaseModel):
    """Описывает безопасную причину пропуска понятным текстом."""

    model_config = ConfigDict(frozen=True)

    message: str
    action: str


class ReloadSummary(BaseModel):
    """Описывает безопасный результат загрузки без тел скиллов."""

    model_config = ConfigDict(frozen=True)

    loaded: int
    skipped: int
    issues: tuple[LoadIssueSummary, ...]


class SkillsPageContext(BaseModel):
    """Описывает строго ограниченный контекст HTML-шаблона."""

    model_config = ConfigDict(frozen=True)

    skills: tuple[SkillSummary, ...]
    query: str
    csrf_token: str
    reload_summary: ReloadSummary
    query_error: str | None
    reload_performed: bool
    reload_unavailable: bool


@dataclass(frozen=True, slots=True)
class _PublicCatalog:
    """Хранит только публичные данные одного поколения."""

    skills: tuple[SkillSummary, ...]
    summary: ReloadSummary

    def search(self, query: str) -> tuple[SkillSummary, ...]:
        """Возвращает публичные карточки этого поколения по запросу.

        Args:
            query: искомая часть имени или подписи.

        Returns:
            Подходящие публичные карточки в сохранённом порядке.

        Raises:
            QueryTooLongError: длина запроса превышает публичный предел.
        """
        _require_valid_query(query)
        needle = query.casefold()
        return tuple(skill for skill in self.skills if _matches(skill, needle))


@dataclass(frozen=True, slots=True)
class _FlashEntry:
    """Хранит публичный результат до ограниченного срока."""

    catalog: _PublicCatalog
    expires_at: float


@dataclass(frozen=True, slots=True)
class SkillsPage:
    """Передаёт контекст страницы вместе с HTTP-статусом."""

    context: SkillsPageContext
    status_code: int


class _CatalogState:
    """Управляет резервацией и одноразовыми публичными результатами."""

    __slots__ = ("_clock", "_flashes", "_reservation", "_state_lock")

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        """Создаёт пустое хранилище с монотонными часами.

        Args:
            clock: источник монотонного времени или стандартные часы процесса.
        """
        self._clock = clock or monotonic
        self._flashes: dict[str, _FlashEntry] = {}
        self._reservation: str | None = None
        self._state_lock = Lock()

    def reserve_reload(self) -> str:
        """Резервирует ограниченный уникальный маркер до чтения файлов.

        Returns:
            Одноразовый идентификатор будущего итога перезагрузки.

        Raises:
            ReloadUnavailableError: допуск занят, ёмкость заполнена или маркер
                не удалось создать.
        """
        with self._state_lock:
            _purge_expired(self._flashes, self._clock())
            if (
                len(self._flashes) >= _MAX_OUTSTANDING_FLASHES
                or self._reservation is not None
            ):
                raise ReloadUnavailableError
            marker = _new_marker(self._flashes)
            self._reservation = marker
            return marker

    def publish_reload(self, marker: str, report: LoadReport) -> None:
        """Завершает резервацию безопасной публичной проекцией.

        Args:
            marker: ранее зарезервированный идентификатор итога.
            report: отчёт той же загрузки, без удержания в хранилище.
        """
        catalog = _PublicCatalog(
            skills=_public_skills(report.search("")),
            summary=_reload_summary(report),
        )
        with self._state_lock:
            if self._reservation != marker:
                raise RuntimeError
            now = self._clock()
            _purge_expired(self._flashes, now)
            self._flashes[marker] = _FlashEntry(
                catalog=catalog,
                expires_at=now + _FLASH_TTL_SECONDS,
            )
            self._reservation = None

    def discard_reservation(self, marker: str) -> None:
        """Освобождает незавершённую резервацию после ошибки или отмены.

        Args:
            marker: идентификатор, который ещё не опубликован.
        """
        with self._state_lock:
            if self._reservation == marker:
                self._reservation = None

    def consume(self, marker: str | None) -> _PublicCatalog | None:
        """Возвращает одноразовый публичный итог по точному маркеру или ничего.

        Args:
            marker: одноразовый идентификатор итога перезагрузки или отсутствие.

        Returns:
            Публичная проекция итога или ничего, если маркер неизвестен или истёк.
        """
        with self._state_lock:
            _purge_expired(self._flashes, self._clock())
            if marker is None or _FLASH_MARKER_PATTERN.fullmatch(marker) is None:
                return None
            entry = self._flashes.pop(marker, None)
            return entry.catalog if entry is not None else None


def build_skills_page(
    catalog: _PublicCatalog,
    query: str,
    csrf_token: str,
    *,
    reload_performed: bool,
    reload_unavailable: bool,
) -> SkillsPage:
    """Собирает единственный вариант контекста страницы каталога.

    Args:
        catalog: публичные карточки и итог одного поколения.
        query: искомая часть имени или подписи.
        csrf_token: секрет CSRF текущего экземпляра приложения.
        reload_performed: признак одноразового итога успешной перезагрузки.
        reload_unavailable: признак предупреждения о недоступной перезагрузке.

    Returns:
        Контекст страницы и HTTP-статус.
    """
    try:
        skills = catalog.search(query)
    except QueryTooLongError:
        context = SkillsPageContext(
            skills=(),
            query="",
            csrf_token=csrf_token,
            reload_summary=catalog.summary,
            query_error=QueryTooLongError.public_message,
            reload_performed=reload_performed,
            reload_unavailable=reload_unavailable,
        )
        return SkillsPage(context=context, status_code=QueryTooLongError.status_code)
    context = SkillsPageContext(
        skills=skills,
        query=query,
        csrf_token=csrf_token,
        reload_summary=catalog.summary,
        query_error=None,
        reload_performed=reload_performed,
        reload_unavailable=reload_unavailable,
    )
    return SkillsPage(context=context, status_code=200)


def _require_valid_query(query: str) -> None:
    if len(query) > MAX_SKILL_QUERY_LENGTH:
        raise QueryTooLongError


def _new_marker(flashes: dict[str, _FlashEntry]) -> str:
    for _attempt in range(_FLASH_MARKER_ATTEMPTS):
        marker = token_urlsafe(_FLASH_MARKER_BYTES)
        if marker not in flashes:
            return marker
    raise ReloadUnavailableError


def public_catalog(capture: RegistryCapture) -> _PublicCatalog:
    """Строит публичное представление одного захваченного поколения.

    Args:
        capture: отчёт и снимок одного опубликованного поколения.

    Returns:
        Публичные карточки и человеческий итог без тел инструкций.
    """
    return _PublicCatalog(
        skills=_public_skills(capture.search("")),
        summary=_reload_summary(capture.report),
    )


def _reload_summary(report: LoadReport) -> ReloadSummary:
    return ReloadSummary(
        loaded=report.loaded,
        skipped=report.skipped,
        issues=tuple(_issue_summary(issue) for issue in report.issues),
    )


def _purge_expired(flashes: dict[str, _FlashEntry], now: float) -> None:
    expired = [marker for marker, entry in flashes.items() if entry.expires_at <= now]
    for marker in expired:
        del flashes[marker]


def _matches(skill: SkillSummary, needle: str) -> bool:
    return needle in skill.name.casefold() or needle in skill.caption.casefold()


def _public_skills(skills: tuple[Skill, ...]) -> tuple[SkillSummary, ...]:
    return tuple(
        SkillSummary(
            name=skill.name,
            caption=skill.caption,
            description=skill.description,
            has_files=skill.has_files,
        )
        for skill in skills
    )


def _issue_summary(issue: LoadIssue) -> LoadIssueSummary:
    _path, message, action = issue_text(issue)
    return LoadIssueSummary(
        message=message,
        action=action,
    )
