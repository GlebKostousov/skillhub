"""Собирает страницу настроек и JSON-снимок живого наложения."""

from typing import TypedDict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from skillhub.runtime import OverlayField, RuntimeSnapshot, RuntimeStore
from skillhub.web._settings_guard import (
    validate_csrf_token,
    validate_settings_request,
)


class SettingsReport(TypedDict):
    """Публичный каталог редактируемых полей без секретов поставщика."""

    fields: list[dict[str, object]]


class _SettingsRoutes:
    """Связывает тонкие HTTP-обработчики настроек с хранилищем снимка."""

    __slots__ = ("_csrf_token", "_store", "_templates")

    def __init__(
        self,
        templates: Jinja2Templates,
        csrf_token: str,
        store: RuntimeStore,
    ) -> None:
        """Сохраняет шаблоны, секрет CSRF и хранилище живого снимка.

        Args:
            templates: шаблоны серверных страниц.
            csrf_token: секрет CSRF текущего экземпляра приложения.
            store: хранилище снимка runtime-настроек на app.state.
        """
        self._templates = templates
        self._csrf_token = csrf_token
        self._store = store

    def settings_json(self) -> SettingsReport:
        """Возвращает каталог редактируемых полей в формате JSON."""
        return _settings_report(self._store.snapshot())

    def settings_page(self, request: Request) -> HTMLResponse:
        """Показывает страницу «Настройки» из того же снимка, что и JSON.

        Args:
            request: входящий HTTP-запрос страницы.
        """
        snapshot = self._store.snapshot()
        return self._templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "csrf_token": self._csrf_token,
                "nav_current": "settings",
                "fields": [_page_field(field) for field in snapshot.fields],
            },
        )

    async def save_settings(self, request: Request) -> SettingsReport:
        """Принимает полную замену наложения и возвращает свежий снимок.

        Args:
            request: недоверенный JSON-запрос сохранения настроек.
        """
        values = await validate_settings_request(request, self._csrf_token)
        self._store.save(values)
        return _settings_report(self._store.snapshot())


def create_settings_router(
    templates: Jinja2Templates,
    *,
    csrf_token: str,
    store: RuntimeStore,
) -> APIRouter:
    """Собирает маршрутизатор страницы и JSON настроек.

    Args:
        templates: шаблоны серверных страниц.
        csrf_token: секрет CSRF из безопасных для URL символов ASCII.
        store: хранилище живого снимка runtime-настроек.

    Returns:
        Маршрутизатор с чтением и полной заменой наложения.

    Raises:
        ValueError: настроенный секрет CSRF не соответствует контракту.
    """
    validate_csrf_token(csrf_token)
    router = APIRouter()
    routes = _SettingsRoutes(templates, csrf_token, store)
    router.add_api_route("/api/settings", routes.settings_json, methods=["GET"])
    router.add_api_route(
        "/api/settings",
        routes.save_settings,
        methods=["POST"],
    )
    router.add_api_route(
        "/settings",
        routes.settings_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    return router


def _settings_report(snapshot: RuntimeSnapshot) -> SettingsReport:
    return {"fields": [_field_payload(field) for field in snapshot.fields]}


def _field_payload(field: OverlayField) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": field.name,
        "value": _public_value(field.value),
        "default": _public_value(field.default),
        "hint": field.hint,
        "kind": field.kind,
    }
    if field.allowed is not None:
        payload["allowed"] = [_public_value(item) for item in field.allowed]
    if field.min is not None:
        payload["min"] = field.min
    if field.max is not None:
        payload["max"] = field.max
    return payload


def _page_field(field: OverlayField) -> dict[str, object]:
    payload = _field_payload(field)
    payload["default_label"] = _label(payload["default"])
    payload["value_label"] = _label(payload["value"])
    payload["range_label"] = _range_label(payload)
    payload["input_value"] = _input_value(field)
    payload["options"] = _options(payload)
    return payload


def _public_value(value: object) -> object:
    if type(value) is tuple:
        return [_public_value(item) for item in value]
    return value


def _label(value: object) -> str:
    if value is None:
        return "n/a"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is list:
        if not value:
            return "пусто"
        return ", ".join(_label(item) for item in value)
    return str(value)


def _range_label(field: dict[str, object]) -> str:
    allowed = field.get("allowed")
    if type(allowed) is list:
        return "Допустимо: " + ", ".join(_label(item) for item in allowed)
    parts: list[str] = []
    if "min" in field:
        parts.append(f"от {field['min']}")
    if "max" in field:
        parts.append(f"до {field['max']}")
    if parts:
        return "Диапазон: " + " ".join(parts)
    return ""


def _options(field: dict[str, object]) -> list[dict[str, str]] | None:
    allowed = field.get("allowed")
    if type(allowed) is not list:
        return None
    return [{"value": _form_value(item), "label": _label(item)} for item in allowed]


def _input_value(field: OverlayField) -> str:
    if field.kind == "string_list":
        items = _public_value(field.value)
        if type(items) is not list:
            return ""
        return "\n".join(str(item) for item in items)
    return _form_value(field.value)


def _form_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)
