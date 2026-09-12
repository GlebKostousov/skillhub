"""Собирает страницу черновика, уточнения и выгрузку протокола в Word."""

import json
import secrets
from collections.abc import Mapping, Sequence
from typing import cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from skillhub.core import SkillHubError
from skillhub.docx_export import build_docx
from skillhub.protocol import (
    Clarification,
    FinalizedProtocol,
    Protocol,
    ProtocolParseError,
    ProtocolTextGenerator,
    apply_answers,
    build_clarifications,
    create_draft,
    parse,
    render,
)
from skillhub.protocol import (
    finalize as finalize_protocol,
)
from skillhub.web._reload_guard import validate_csrf_token
from skillhub.web.errors import RequestTooLargeError, UnsupportedFormError

MAX_PROTOCOL_BODY_BYTES = 1_048_576
_JSON_TYPE = "application/json"
_DRAFT_INSTRUCTION = "Собери нормативный протокол встречи."
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_DISPOSITION = 'attachment; filename="protocol.docx"'


class GenerationUnavailableError(SkillHubError):
    """Описывает отсутствие порта генерации черновика."""

    code = "generation_unavailable"
    status_code = 503
    public_message = "Генерация черновика недоступна."


class ProtocolForbiddenError(SkillHubError):
    """Описывает отказ проверки источника или секрета CSRF."""

    code = "protocol_forbidden"
    status_code = 403
    public_message = "Изменение протокола запрещено."


class ProtocolInvalidRequestError(SkillHubError):
    """Описывает отказ разобрать тело изменяющего запроса."""

    code = "protocol_invalid_request"
    status_code = 422
    public_message = "Тело запроса имеет неверный формат."


class _ProtocolRoutes:
    """Связывает тонкие HTTP-обработчики протокола с зависимостями."""

    __slots__ = ("_csrf_token", "_generator", "_templates")

    def __init__(
        self,
        templates: Jinja2Templates,
        csrf_token: str,
        generator: ProtocolTextGenerator | None,
    ) -> None:
        """Сохраняет зависимости обработчиков протокола.

        Args:
            templates: шаблоны серверных страниц.
            csrf_token: секрет CSRF текущего экземпляра приложения.
            generator: порт генерации или отсутствие генерации.
        """
        self._templates = templates
        self._csrf_token = csrf_token
        self._generator = generator

    def page(self, request: Request) -> Response:
        """Показывает страницу черновика и выгрузки протокола."""
        return self._templates.TemplateResponse(
            request=request,
            name="protocol.html",
            context={
                "csrf_token": self._csrf_token,
                "generation_available": self._generator is not None,
            },
        )

    async def draft(self, request: Request) -> Response:
        """Собирает черновик протокола из транскрипции."""
        payload = await self._read_json(request)
        generator = self._require_generator()
        transcript = _string_field(payload, "transcript")
        try:
            protocol = create_draft(generator, _DRAFT_INSTRUCTION, transcript)
        except ProtocolParseError as exc:
            return _parse_error_response(exc)
        return JSONResponse(content=_draft_payload(protocol))

    async def export_docx(self, request: Request) -> Response:
        """Собирает Word-файл из принятого текста протокола."""
        payload = await self._read_json(request)
        text = _string_field(payload, "text")
        try:
            protocol = parse(text)
        except ProtocolParseError as exc:
            return _parse_error_response(exc)
        return _docx_response(build_docx(protocol))

    async def clarifications(self, request: Request) -> Response:
        """Возвращает список уточнений по тексту протокола."""
        payload = await self._read_json(request)
        text = _string_field(payload, "text")
        try:
            protocol = parse(text)
        except ProtocolParseError as exc:
            return _parse_error_response(exc)
        return JSONResponse(content=_clarifications_payload(protocol))

    async def answer(self, request: Request) -> Response:
        """Применяет одно решение уточнения к тексту протокола."""
        payload = await self._read_json(request)
        text = _string_field(payload, "text")
        try:
            rebuilt = rebuild_protocol_text(text, (_decision(payload),))
            updated = parse(rebuilt)
        except ProtocolParseError as exc:
            return _parse_error_response(exc)
        content = _clarifications_payload(updated)
        content["text"] = rebuilt
        return JSONResponse(content=content)

    async def finalize(self, request: Request) -> Response:
        """Собирает итоговый протокол из закрытых уточнений и материала."""
        payload = await self._read_json(request)
        text = _string_field(payload, "text")
        material = _string_field(payload, "material")
        answers = _answers_field(payload)
        try:
            protocol = parse(text)
            result = finalize_protocol(protocol, answers, material)
        except ProtocolParseError as exc:
            return _parse_error_response(exc)
        return JSONResponse(content=_finalize_payload(result))

    def _require_generator(self) -> ProtocolTextGenerator:
        if self._generator is None:
            raise GenerationUnavailableError
        return self._generator

    async def _read_json(self, request: Request) -> dict[str, object]:
        _require_same_origin(request)
        _require_json_type(request)
        body = await _read_bounded_body(request)
        payload = _parse_json_object(body)
        _require_csrf(payload, self._csrf_token)
        return payload


def create_protocol_router(
    templates: Jinja2Templates,
    *,
    csrf_token: str,
    generator: ProtocolTextGenerator | None,
) -> APIRouter:
    """Собирает маршрутизатор страницы и выгрузки протокола.

    Args:
        templates: шаблоны серверных страниц.
        csrf_token: секрет CSRF из безопасных для URL символов ASCII.
        generator: порт генерации или ``None``, если генерация недоступна.

    Returns:
        Маршрутизатор с страницей черновика и изменяющими швами.

    Raises:
        ValueError: настроенный секрет CSRF не соответствует контракту.
    """
    validate_csrf_token(csrf_token)
    router = APIRouter()
    routes = _ProtocolRoutes(templates, csrf_token, generator)
    router.add_api_route(
        "/protocol",
        routes.page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    router.add_api_route(
        "/protocol/draft",
        routes.draft,
        methods=["POST"],
        response_model=None,
    )
    router.add_api_route(
        "/protocol/docx",
        routes.export_docx,
        methods=["POST"],
        response_model=None,
    )
    router.add_api_route(
        "/protocol/clarifications",
        routes.clarifications,
        methods=["POST"],
        response_model=None,
    )
    router.add_api_route(
        "/protocol/answer",
        routes.answer,
        methods=["POST"],
        response_model=None,
    )
    router.add_api_route(
        "/protocol/finalize",
        routes.finalize,
        methods=["POST"],
        response_model=None,
    )
    return router


def _require_same_origin(request: Request) -> None:
    origin = _one_header(request, "origin")
    host = _one_header(request, "host")
    if origin is None or host is None:
        raise ProtocolForbiddenError
    _reject_foreign_origin(origin, request.url.scheme, host)


def _reject_foreign_origin(origin: str, scheme: str, host: str) -> None:
    if origin != f"{scheme}://{host}":
        raise ProtocolForbiddenError


def _one_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) != 1:
        return None
    return values[0]


def _require_json_type(request: Request) -> None:
    if _media_type(request) != _JSON_TYPE:
        raise UnsupportedFormError


def _media_type(request: Request) -> str:
    content_type = _one_header(request, "content-type")
    if content_type is None:
        return ""
    return content_type.partition(";")[0].strip().casefold()


async def _read_bounded_body(request: Request) -> bytes:
    _reject_declared_length(request)
    return await _collect_body(request)


def _reject_declared_length(request: Request) -> None:
    raw_length = _one_header(request, "content-length")
    if raw_length is None:
        return
    _reject_invalid_length(raw_length)


def _reject_invalid_length(raw_length: str) -> None:
    declared = _parse_declared_length(raw_length)
    if declared < 0 or declared > MAX_PROTOCOL_BODY_BYTES:
        raise RequestTooLargeError


def _parse_declared_length(raw_length: str) -> int:
    try:
        parsed: int | None = int(raw_length)
    except ValueError:
        parsed = None
    if parsed is None:
        raise RequestTooLargeError
    return parsed


async def _collect_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        _reject_collected_size(size)
        chunks.append(chunk)
    return b"".join(chunks)


def _reject_collected_size(size: int) -> None:
    if size > MAX_PROTOCOL_BODY_BYTES:
        raise RequestTooLargeError


def _parse_json_object(body: bytes) -> dict[str, object]:
    payload = _load_json(_decode_utf8(body))
    return _require_mapping(payload)


def _decode_utf8(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolInvalidRequestError from exc


def _load_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolInvalidRequestError from exc


def _require_mapping(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ProtocolInvalidRequestError
    return cast("dict[str, object]", payload)


def _require_csrf(payload: dict[str, object], expected: str) -> None:
    submitted = payload.get("csrf_token")
    if not isinstance(submitted, str):
        raise ProtocolForbiddenError
    _reject_mismatched_token(submitted, expected)


def _reject_mismatched_token(submitted: str, expected: str) -> None:
    if not _tokens_match(submitted, expected):
        raise ProtocolForbiddenError


def _tokens_match(submitted: str, expected: str) -> bool:
    try:
        left = submitted.encode("ascii")
        right = expected.encode("ascii")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(left, right)


def _string_field(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ProtocolInvalidRequestError
    return value


def _draft_payload(protocol: Protocol) -> dict[str, object]:
    payload = cast("dict[str, object]", protocol.model_dump())
    payload["text"] = render(protocol)
    return payload


def rebuild_protocol_text(
    base_text: str,
    decisions: Sequence[Mapping[str, object]],
) -> str:
    """Собирает Markdown, заново применяя решения к базовому тексту.

    Страница хранит базовый текст последнего успешного списка уточнений
    и при отмене строки заново применяет оставшиеся решения через этот шов.

    Args:
        base_text: нормативный Markdown до локальных решений вкладки.
        decisions: оставшиеся ответы и пропуски в порядке таблицы.

    Returns:
        Текст протокола после применения решений.
    """
    return render(apply_answers(parse(base_text), decisions))


def _decision(payload: dict[str, object]) -> dict[str, object]:
    decision: dict[str, object] = {
        "id": _string_field(payload, "id"),
        "action": _string_field(payload, "action"),
    }
    if "value" in payload:
        decision["value"] = payload["value"]
    return decision


def _answers_field(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get("answers")
    if not isinstance(raw, list):
        raise ProtocolInvalidRequestError
    return [_require_decision_mapping(item) for item in raw]


def _require_decision_mapping(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        raise ProtocolInvalidRequestError
    return cast("dict[str, object]", item)


def _finalize_payload(result: FinalizedProtocol) -> dict[str, object]:
    return {
        "text": render(result.protocol),
        "unconfirmed": [
            {"target": item.target, "reason": item.reason}
            for item in result.unconfirmed
        ],
    }


def _clarifications_payload(protocol: Protocol) -> dict[str, object]:
    return {
        "clarifications": [
            _clarification_item(item) for item in build_clarifications(protocol)
        ]
    }


def _clarification_item(item: Clarification) -> dict[str, str]:
    return {
        "id": item.id,
        "target": item.target,
        "reason": item.reason,
        "hint": item.hint,
        "status": item.status,
    }


def _docx_response(content: bytes) -> Response:
    return Response(
        content=content,
        media_type=_DOCX_TYPE,
        headers={
            "Content-Disposition": _DOCX_DISPOSITION,
            "Cache-Control": "no-store",
        },
    )


def _parse_error_response(exc: ProtocolParseError) -> JSONResponse:
    structlog.get_logger(__name__).warning(
        "http.expected_error",
        error_code=exc.code,
        status_code=exc.status_code,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.public_message,
                "line": exc.line,
                "expected": exc.expected,
                "got": exc.got,
            }
        },
    )
