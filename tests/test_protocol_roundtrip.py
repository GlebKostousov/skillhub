"""Проверяет воспроизведение модели протокола через публичный фасад."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from skillhub.protocol import (
    GRAMMAR_VERSION,
    PLACEHOLDER,
    Protocol,
    ProtocolTask,
    parse,
    render,
)

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


def test_pipe_in_task_fields_is_rejected() -> None:
    """Проверяет отказ полей задачи с вертикальной чертой таблицы."""
    with pytest.raises(ValidationError):
        ProtocolTask(title="A|B", assignee="Анна", due="2026-09-15")
    with pytest.raises(ValidationError):
        ProtocolTask(title="Задача", assignee="Анна|Борис", due="2026-09-15")
    with pytest.raises(ValidationError):
        ProtocolTask(title="Задача", assignee="Анна", due="2026-09|15")


def test_pipe_in_rendered_task_cells_roundtrips_as_slash() -> None:
    """Проверяет разбор воспроизведения после замены вертикальной черты."""
    protocol = Protocol.model_construct(
        title="Тема",
        date=PLACEHOLDER,
        participants=("Анна",),
        discussion=("пункт",),
        decisions=("решение",),
        tasks=(
            ProtocolTask.model_construct(
                title="A|B",
                assignee="C|D",
                due="E|F",
            ),
        ),
        open_questions=("вопрос",),
        grammar_version=GRAMMAR_VERSION,
    )
    parsed = parse(render(protocol))

    assert parsed.tasks == (
        ProtocolTask(title="A/B", assignee="C/D", due="E/F"),
    )


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
