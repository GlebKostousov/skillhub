"""Удаляет запрещённые управляющие символы XML из текста Word."""

_ALLOWED_CONTROLS = frozenset({0x9, 0xA, 0xD})
_C0_LIMIT = 0x20
_MAX_BMP = 0xD7FF
_PUA_START = 0xE000
_PUA_END = 0xFFFD
_SUPP_START = 0x10000
_SUPP_END = 0x10FFFF


def sanitize_docx_text(value: str) -> str:
    """Возвращает текст без запрещённых управляющих символов XML.

    Args:
        value: исходная строка модели протокола.

    Returns:
        Строка, пригодная для текстового узла Word.
    """
    return "".join(character for character in value if _is_xml_text(ord(character)))


def _is_xml_text(code: int) -> bool:
    if code < _C0_LIMIT:
        return code in _ALLOWED_CONTROLS
    return _is_xml_scalar(code)


def _is_xml_scalar(code: int) -> bool:
    if code <= _MAX_BMP:
        return True
    return _is_supplementary(code)


def _is_supplementary(code: int) -> bool:
    if _PUA_START <= code <= _PUA_END:
        return True
    return _SUPP_START <= code <= _SUPP_END
