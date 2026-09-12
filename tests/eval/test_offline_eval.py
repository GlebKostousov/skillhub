"""Проверяет офлайн-корпус injection и отчёт границ."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _REPOSITORY_ROOT / "eval" / "run_eval.py"
_CASES_PATH = _REPOSITORY_ROOT / "eval" / "security_eval_cases.md"
_MATRIX_PATH = _REPOSITORY_ROOT / "eval" / "classifier_matrix.md"

_REQUIRED_FAMILIES = frozenset(
    {
        "transcript-instruction",
        "format-change",
        "skill-change",
        "language-change",
        "authority-change",
        "fake-tool",
        "fake-secret",
        "delimiter-confusion",
        "markdown-html",
    }
)


def _load_runner() -> ModuleType:
    """Загружает офлайн-runner без добавления eval в sys.path.

    Returns:
        Модуль `eval/run_eval.py`.
    """
    spec = importlib.util.spec_from_file_location(
        "skillhub_security_eval",
        _RUNNER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Запускает `eval/run_eval.py` отдельным процессом.

    Args:
        arguments: аргументы командной строки runner.

    Returns:
        Завершённый процесс со стандартными потоками.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_RUNNER_PATH), *arguments],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_offline_runner_exits_zero_and_keeps_matrix() -> None:
    """Проверяет, что `--offline` завершается успешно и только читает матрицу."""
    before = _MATRIX_PATH.read_bytes()

    completed = _run_cli("--offline")

    assert completed.returncode == 0, completed.stderr
    assert _MATRIX_PATH.read_bytes() == before
    assert "границы:" in completed.stdout
    assert "остаточный смысл:" in completed.stdout
    assert "абсолютная защита" in completed.stdout


def test_live_eval_is_not_a_merge_gate() -> None:
    """Проверяет, что запуск без `--offline` не считается условием merge."""
    completed = _run_cli()

    assert completed.returncode == 2
    assert "не является условием merge" in completed.stdout


def test_corpus_covers_required_attack_families() -> None:
    """Проверяет, что корпус содержит обязательные семейства атак."""
    runner = _load_runner()
    families = {case.family for case in runner.load_cases(_CASES_PATH)}

    assert families >= _REQUIRED_FAMILIES


def test_report_separates_boundary_pass_from_residual() -> None:
    """Проверяет разделение обхода границы и остаточной ошибки модели."""
    runner = _load_runner()

    report = runner.evaluate_offline()

    assert report.boundary_failed == 0
    assert any(item.status == "pass" for item in report.findings)
    assert any(item.status == "residual" for item in report.findings)
    assert all(item.status != "fail" for item in report.findings)
    rendered = runner.render_report(report)
    assert "границы:" in rendered
    assert "остаточный смысл:" in rendered


def test_material_does_not_reach_classifier() -> None:
    """Проверяет изоляцию материала от классификатора на корпусе."""
    runner = _load_runner()

    for case in runner.load_cases(_CASES_PATH):
        classify, _generate, _outcome = runner.run_case(case)
        sent = runner.request_text(classify.requests[0])
        assert case.material not in sent


def test_requests_have_no_tools_field() -> None:
    """Проверяет, что запросы шлюза не содержат инструментов."""
    runner = _load_runner()

    for case in runner.load_cases(_CASES_PATH):
        classify, generate, _outcome = runner.run_case(case)
        for request in (*classify.requests, *generate.requests):
            assert not hasattr(request, "tools")
            assert set(type(request).__dataclass_fields__) == {"messages"}


def test_handler_stays_after_classification() -> None:
    """Проверяет, что материал не выбирает обработчик после классификации."""
    runner = _load_runner()

    for case in runner.load_cases(_CASES_PATH):
        _classify, generate, outcome = runner.run_case(case)
        assert outcome.selected_skill == case.expected_skill
        if case.expected_skill is None:
            assert generate.requests == []
            continue
        system = runner.system_text(generate.requests[0])
        assert system == runner.trusted_body(case.expected_skill)
        assert case.material not in system


def test_parser_rejects_malformed_generate() -> None:
    """Проверяет, что сломанный ответ модели не обходит parser."""
    runner = _load_runner()
    kinds = {"broken-heading", "html-payload", "tool-json"}

    checked = 0
    for case in runner.load_cases(_CASES_PATH):
        if case.generate_kind not in kinds:
            continue
        _classify, _generate, outcome = runner.run_case(case)
        assert outcome.outcome == "provider_error"
        assert outcome.text is None
        checked += 1

    assert checked >= 2
