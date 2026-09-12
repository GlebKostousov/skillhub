"""Проверяет разбор принятого Markdown на публичном фасаде."""

from pathlib import Path

import pytest

from skillhub.protocol import (
    PLACEHOLDER,
    Protocol,
    ProtocolParseError,
    ProtocolTask,
    parse,
)

_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "meeting-protocol"
    / "references"
    / "example.md"
)


def test_example_fixture_parses_to_protocol() -> None:
    """Проверяет разбор нормативного примера и задачу с заполнителем."""
    protocol = parse(_EXAMPLE.read_text(encoding="utf-8"))

    assert protocol == Protocol(
        title="Подготовка квартального отчёта",
        date="2026-09-12",
        participants=("Анна", "Борис"),
        discussion=(
            "Команда сверила состав квартального отчёта и доступность исходных данных.",
        ),
        decisions=("Включить в отчёт показатели продаж и поддержки.",),
        tasks=(
            ProtocolTask(
                title="Проверить показатели продаж",
                assignee="Анна",
                due="2026-09-15",
            ),
            ProtocolTask(
                title="Подготовить данные поддержки",
                assignee=PLACEHOLDER,
                due="до следующей встречи",
            ),
        ),
        open_questions=("Нужно определить ответственного за данные поддержки.",),
    )
    assert protocol.tasks[1].assignee == PLACEHOLDER


def test_oversized_input_is_rejected() -> None:
    """Проверяет отказ до разбора при превышении предельного размера."""
    with pytest.raises(ProtocolParseError) as caught:
        parse("x" * 65_537)

    assert caught.value.line == 1
    assert caught.value.expected == "не более 65536 символов"
    assert caught.value.got == "65537"


_VALID = """# Протокол встречи: Тема

**Дата:** 2026-09-12
**Участники:** Анна

## Обсуждение
- пункт

## Решения
- решение

## Задачи
- —

## Открытые вопросы
- вопрос
"""

_TABLE = """# Протокол встречи: Тема

**Дата:** 2026-09-12
**Участники:** Анна

## Обсуждение
- пункт

## Решения
- решение

## Задачи
| Задача | Ответственный | Срок |
|---|---|---|
| Сделать | Анна | 2026-09-15 |

## Открытые вопросы
- вопрос
"""


def _expect_error(text: str, line: int, expected: str, got: str) -> None:
    """Проверяет координаты одного структурного отказа.

    Args:
        text: исходный Markdown.
        line: ожидаемый номер строки.
        expected: ожидаемый структурный элемент.
        got: ожидаемый фактический фрагмент.
    """
    with pytest.raises(ProtocolParseError) as caught:
        parse(text)
    assert caught.value.line == line
    assert caught.value.expected == expected
    assert caught.value.got == got


def test_wrong_h1_prefix_is_rejected() -> None:
    """Проверяет отказ при чужом префиксе единственного H1."""
    _expect_error(
        _VALID.replace("# Протокол встречи: Тема", "# Итоги встречи: Тема"),
        1,
        "# Протокол встречи:",
        "# Итоги встречи: Тема",
    )


def test_second_h1_is_rejected() -> None:
    """Проверяет отказ при втором заголовке первого уровня."""
    _expect_error(
        _VALID.replace(
            "# Протокол встречи: Тема\n\n",
            "# Протокол встречи: Тема\n# Протокол встречи: Ещё\n\n",
        ),
        2,
        "**Дата:**",
        "# Протокол встречи: Ещё",
    )


def test_empty_title_is_rejected() -> None:
    """Проверяет отказ при пустой теме после префикса H1."""
    _expect_error(_VALID.replace("Тема", ""), 1, "тема встречи", "# Протокол встречи: ")


def test_swapped_metadata_is_rejected() -> None:
    """Проверяет отказ при перестановке строк даты и участников."""
    _expect_error(
        _VALID.replace(
            "**Дата:** 2026-09-12\n**Участники:** Анна",
            "**Участники:** Анна\n**Дата:** 2026-09-12",
        ),
        3,
        "**Дата:**",
        "**Участники:** Анна",
    )


def test_missing_date_is_rejected() -> None:
    """Проверяет отказ при отсутствии строки даты сразу после H1."""
    _expect_error(
        _VALID.replace("**Дата:** 2026-09-12\n", ""),
        3,
        "**Дата:**",
        "**Участники:** Анна",
    )


def test_missing_participants_is_rejected() -> None:
    """Проверяет отказ при отсутствии строки участников после даты."""
    _expect_error(
        _VALID.replace("**Участники:** Анна\n", ""),
        4,
        "**Участники:**",
        "",
    )


def test_empty_participant_name_is_rejected() -> None:
    """Проверяет отказ при пустом имени в списке участников."""
    _expect_error(
        _VALID.replace("**Участники:** Анна", "**Участники:** Анна,"),
        4,
        "**Участники:**",
        "Анна,",
    )


def test_omitted_discussion_heading_is_rejected() -> None:
    """Проверяет отказ при опущенном обязательном H2."""
    _expect_error(
        _VALID.replace("## Обсуждение\n- пункт\n\n", ""),
        6,
        "## Обсуждение",
        "## Решения",
    )


def test_omitted_open_questions_heading_is_rejected() -> None:
    """Проверяет отказ при отсутствии завершающего обязательного H2."""
    _expect_error(
        _VALID.replace("\n## Открытые вопросы\n- вопрос\n", ""),
        13,
        "## Открытые вопросы",
        "",
    )


def test_extra_heading_is_rejected() -> None:
    """Проверяет отказ при лишнем H2 после четырёх разделов."""
    _expect_error(
        _VALID + "\n## Приложение\n- ещё\n",
        18,
        "конец протокола",
        "## Приложение",
    )


def test_reordered_headings_are_rejected() -> None:
    """Проверяет отказ при перестановке обязательных H2."""
    _expect_error(
        _VALID.replace(
            "## Решения\n- решение\n\n## Задачи\n- —",
            "## Задачи\n- —\n\n## Решения\n- решение",
        ),
        9,
        "## Решения",
        "## Задачи",
    )


def test_renamed_heading_is_rejected() -> None:
    """Проверяет отказ при переименовании обязательного H2."""
    _expect_error(
        _VALID.replace("## Обсуждение", "## Итоги"), 6, "## Обсуждение", "## Итоги"
    )


def test_empty_heading_without_placeholder_is_rejected() -> None:
    """Проверяет отказ пустого H2 без единственного пункта-заполнителя."""
    _expect_error(_VALID.replace("- пункт\n", ""), 7, "- —", "")


def test_wrong_bullet_marker_is_rejected() -> None:
    """Проверяет отказ при чужом маркере пункта списка."""
    _expect_error(_VALID.replace("- пункт", "* пункт"), 7, "- пункт", "* пункт")


def test_empty_bullet_text_is_rejected() -> None:
    """Проверяет отказ пункта списка без текста после маркера."""
    _expect_error(_VALID.replace("- пункт", "- "), 7, "- пункт", "- ")


def test_empty_tasks_heading_without_placeholder_is_rejected() -> None:
    """Проверяет отказ пустого раздела задач без пункта-заполнителя."""
    _expect_error(_VALID.replace("- —\n", ""), 13, "- —", "")


def test_task_header_without_separator_is_rejected() -> None:
    """Проверяет отказ таблицы задач, в которой нет разделителя."""
    _expect_error(
        _TABLE.replace(
            (
                "| Задача | Ответственный | Срок |\n"
                "|---|---|---|\n"
                "| Сделать | Анна | 2026-09-15 |\n"
            ),
            "| Задача | Ответственный | Срок |\n",
        ),
        14,
        "|---|---|---|",
        "",
    )


def test_task_row_without_pipes_is_rejected() -> None:
    """Проверяет отказ строки задачи без табличных ограничителей."""
    _expect_error(
        _TABLE.replace("| Сделать | Анна | 2026-09-15 |", "Сделать Анна 2026-09-15"),
        15,
        "три колонки",
        "Сделать Анна 2026-09-15",
    )


def test_empty_task_table_is_rejected() -> None:
    """Проверяет отказ пустой таблицы задач вместо пункта-заполнителя."""
    _expect_error(
        _TABLE.replace("| Сделать | Анна | 2026-09-15 |\n", ""),
        15,
        "строка задачи",
        "",
    )


def test_missing_task_header_is_rejected() -> None:
    """Проверяет отказ таблицы задач без нормативного заголовка."""
    _expect_error(
        _TABLE.replace("| Задача | Ответственный | Срок |\n", ""),
        13,
        "| Задача | Ответственный | Срок |",
        "|---|---|---|",
    )


def test_missing_task_separator_is_rejected() -> None:
    """Проверяет отказ таблицы задач без обязательного разделителя."""
    _expect_error(
        _TABLE.replace("|---|---|---|\n", ""),
        14,
        "|---|---|---|",
        "| Сделать | Анна | 2026-09-15 |",
    )


def test_pipe_inside_task_cell_is_rejected() -> None:
    """Проверяет отказ при символе таблицы внутри ячейки задачи."""
    _expect_error(
        _TABLE.replace(
            "| Сделать | Анна | 2026-09-15 |", "| Сделать | Анна|Борис | 2026-09-15 |"
        ),
        15,
        "три колонки",
        "| Сделать | Анна|Борис | 2026-09-15 |",
    )


def test_two_column_task_row_is_rejected() -> None:
    """Проверяет отказ строки задачи с числом колонок меньше трёх."""
    _expect_error(
        _TABLE.replace("| Сделать | Анна | 2026-09-15 |", "| Сделать | Анна |"),
        15,
        "три колонки",
        "| Сделать | Анна |",
    )


def test_empty_task_cell_is_rejected() -> None:
    """Проверяет отказ пустой ячейки задачи без литерала пропуска."""
    _expect_error(
        _TABLE.replace(
            "| Сделать | Анна | 2026-09-15 |", "| Сделать |  | 2026-09-15 |"
        ),
        15,
        "значение или —",
        "| Сделать |  | 2026-09-15 |",
    )


def test_markup_in_item_is_plain_text() -> None:
    """Проверяет, что разметка в пункте сохраняется как обычный текст."""
    protocol = parse(_VALID.replace("пункт", "<script>alert(1)</script>"))

    assert protocol.discussion == ("<script>alert(1)</script>",)


def test_unknown_skip_words_stay_as_data() -> None:
    """Проверяет отсутствие ремонта чужих слов пропуска в данные."""
    protocol = parse(_VALID.replace("- пункт", "- TBD"))

    assert protocol.discussion == ("TBD",)


def test_repeated_parse_returns_equal_model() -> None:
    """Проверяет равенство модели при повторном разборе того же текста."""
    assert parse(_VALID) == parse(_VALID)
