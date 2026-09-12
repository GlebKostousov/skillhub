"""Разбирает и проверяет ограниченное содержимое одного SKILL.md."""

import re

import yaml
from yaml.tokens import AliasToken, AnchorToken, TagToken

from skillhub.registry._errors import reject

_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1_024
_NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_YAML_GRAPH_TOKENS = (AliasToken, AnchorToken, TagToken)

type ParsedSkill = tuple[str, str, str, str]


def parse_document(payload: bytes, directory_name: str) -> ParsedSkill:
    """Возвращает проверенные поля одного документа.

    Args:
        payload: ограниченные байты SKILL.md.
        directory_name: исходное имя каталога при отсутствии поля name.

    Returns:
        Имя, подпись, описание и Markdown-тело.

    Raises:
        InvalidSkillError: содержимое не удовлетворяет договору реестра.
    """
    text = _decode(payload)
    front_matter, body = _document_parts(text.replace("\r\n", "\n").replace("\r", "\n"))
    metadata = _load_yaml(front_matter)
    name = _validated_name(metadata, directory_name)
    description = _validated_description(metadata)
    return name, _validated_caption(metadata, name), description, body


def _decode(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return reject("invalid_utf8")


def _document_parts(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    _validate_opening_delimiter(lines)
    closing_index = _closing_delimiter_index(lines)
    body = "".join(lines[closing_index + 1 :]).strip()
    if not body:
        return reject("empty_body")
    return "".join(lines[1:closing_index]), body


def _validate_opening_delimiter(lines: list[str]) -> None:
    if not lines or lines[0].rstrip("\r\n") != "---":
        reject("invalid_yaml")


def _closing_delimiter_index(lines: list[str]) -> int:
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return index
    return reject("invalid_yaml")


def _load_yaml(front_matter: str) -> dict[object, object]:
    try:
        _reject_yaml_graph(front_matter)
        loaded = yaml.safe_load(front_matter)
    except (OverflowError, RecursionError, ValueError, yaml.YAMLError):
        return reject("invalid_yaml")
    return _validated_mapping(loaded)


def _reject_yaml_graph(front_matter: str) -> None:
    if any(isinstance(token, _YAML_GRAPH_TOKENS) for token in yaml.scan(front_matter)):
        reject("invalid_yaml")


def _validated_mapping(loaded: object) -> dict[object, object]:
    if type(loaded) is not dict:
        return reject("invalid_yaml")
    return loaded


def _validated_name(
    metadata: dict[object, object],
    directory_name: str,
) -> str:
    raw_name = metadata.get("name")
    name = directory_name if raw_name is None else raw_name
    if (
        type(name) is not str
        or len(name) > _MAX_NAME_LENGTH
        or _NAME_PATTERN.fullmatch(name) is None
    ):
        return reject("invalid_name")
    return name


def _validated_description(metadata: dict[object, object]) -> str:
    description = metadata.get("description")
    if description is None:
        return reject("missing_description")
    if type(description) is not str:
        return reject("invalid_yaml")
    return _validated_description_text(description)


def _validated_description_text(description: str) -> str:
    if not description.strip():
        return reject("missing_description")
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        return reject("description_too_long")
    return description


def _validated_caption(metadata: dict[object, object], name: str) -> str:
    caption = metadata.get("caption")
    if caption is None:
        return name
    if type(caption) is not str:
        return reject("invalid_yaml")
    return caption
