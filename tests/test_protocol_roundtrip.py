"""Проверяет воспроизведение модели протокола через публичный фасад."""

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from skillhub.protocol import PLACEHOLDER, Protocol, ProtocolTask, parse, render

_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "meeting-protocol"
    / "references"
    / "example.md"
)


def test_example_parse_render_roundtrip() -> None:
    """Проверяет равенство модели после разбора воспроизведённого примера."""
    protocol = parse(_EXAMPLE.read_text(encoding="utf-8"))

    assert parse(render(protocol)) == protocol


def test_empty_sections_render_as_placeholder_and_roundtrip() -> None:
    """Проверяет единственную нормализацию пустого раздела в заполнитель."""
    protocol = Protocol(
        title="Тема",
        date=PLACEHOLDER,
        participants=(),
        discussion=(),
        decisions=(),
        tasks=(),
        open_questions=(),
    )
    rendered = render(protocol)

    assert "## Обсуждение\n- —" in rendered
    assert "## Решения\n- —" in rendered
    assert "## Задачи\n- —" in rendered
    assert "## Открытые вопросы\n- —" in rendered
    assert "| Задача |" not in rendered
    assert parse(rendered) == protocol


_SAFE_CHARS = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters=" -.",
    blacklist_characters="|#\n\r,",
)
_TOKEN = (
    st.text(_SAFE_CHARS, min_size=1, max_size=24)
    .map(str.strip)
    .filter(lambda token: token not in {"", PLACEHOLDER})
)
_TASK = st.builds(ProtocolTask, title=_TOKEN, assignee=_TOKEN, due=_TOKEN)
_PROTOCOLS = st.builds(
    Protocol,
    title=_TOKEN,
    date=st.one_of(st.just(PLACEHOLDER), _TOKEN),
    participants=st.lists(_TOKEN, max_size=4).map(tuple),
    discussion=st.lists(_TOKEN, max_size=4).map(tuple),
    decisions=st.lists(_TOKEN, max_size=4).map(tuple),
    tasks=st.lists(_TASK, max_size=3).map(tuple),
    open_questions=st.lists(_TOKEN, max_size=4).map(tuple),
)


@given(_PROTOCOLS)
@settings(max_examples=40)
def test_parse_render_roundtrip_for_any_protocol(protocol: Protocol) -> None:
    """Проверяет равенство модели после воспроизведения и повторного разбора."""
    assert parse(render(protocol)) == protocol
