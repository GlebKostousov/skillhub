"""Хранит проверяемый снимок runtime-настроек в файле рядом с процессом."""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

import structlog

from skillhub.runtime._constants import OVERLAY_FILENAME, TEMP_PREFIX
from skillhub.runtime._errors import InvalidOverlayError, OverlayUnavailableError
from skillhub.runtime._models import OverlayValues, RuntimeSnapshot, catalog_fields
from skillhub.runtime._schema import overlay_payload, parse_overlay, seed_values


class RuntimeStore:
    """Собирает замороженный снимок из посева и файла runtime-overlay.json."""

    def __init__(
        self,
        model_limits: Mapping[str, int],
        *,
        daily_budget_nanos: int | None = None,
    ) -> None:
        """Запоминает лимиты моделей и посевной дневной потолок.

        Args:
            model_limits: допустимые модели и потолок max_tokens.
            daily_budget_nanos: посевной дневной потолок или его отсутствие.
        """
        self._limits = dict(model_limits)
        self._defaults = seed_values(daily_budget_nanos, model_limits)

    def snapshot(self) -> RuntimeSnapshot:
        """Возвращает замороженный каталог из посева или файла.

        Returns:
            Каталог полей с текущими значениями, посевом и подсказками.
        """
        values = _load_values(self._overlay_path(), self._limits, self._defaults)
        return RuntimeSnapshot(
            values=values,
            fields=catalog_fields(values, self._defaults, self._limits),
        )

    def save(self, values: Mapping[str, object]) -> None:
        """Проверяет документ и атомарно записывает runtime-overlay.json.

        Args:
            values: полный набор редактируемых полей.

        Raises:
            InvalidOverlayError: схема, ключ или диапазон отклонены.
            OverlayUnavailableError: атомарная запись не удалась.
        """
        parsed = parse_overlay(dict(values), self._limits)
        _write_atomic(self._overlay_path(), overlay_payload(parsed))

    def _overlay_path(self) -> Path:
        return Path(OVERLAY_FILENAME)


def _load_values(
    path: Path,
    model_limits: Mapping[str, int],
    defaults: OverlayValues,
) -> OverlayValues:
    if not path.is_file():
        return defaults
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _log_invalid_overlay()
        return defaults
    try:
        return parse_overlay(raw, model_limits)
    except InvalidOverlayError:
        _log_invalid_overlay()
        return defaults


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    tmp_path: Path | None = None
    try:
        handle, tmp_name = tempfile.mkstemp(
            prefix=TEMP_PREFIX,
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise OverlayUnavailableError from None


def _log_invalid_overlay() -> None:
    structlog.get_logger(__name__).warning(
        "runtime.invalid_overlay",
        error_code="invalid_overlay",
    )
