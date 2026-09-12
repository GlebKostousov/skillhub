# ruff: noqa: E402, INP001
"""Прогоняет офлайн-демонстрацию пяти режимов, протокола и сводки eval."""

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from zipfile import ZipFile

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from skillhub.assistant import (
    Assistant,
    AssistantOutcome,
    PromptSkillHandler,
    ProtocolSkillHandler,
    SkillHandlerRegistry,
)
from skillhub.classifier import SkillClassifier
from skillhub.docx_export import build_docx
from skillhub.llm import (
    FakeLlmGateway,
    LlmMessage,
    LlmRequest,
    LlmResult,
    LlmUsage,
    MeteredLlmGateway,
)
from skillhub.protocol import (
    PLACEHOLDER,
    Clarification,
    build_clarifications,
    finalize,
    parse,
)
from skillhub.registry import Skill, SkillRegistry
from skillhub.usage import (
    DailyBudgetExceededError,
    TariffCatalog,
    UsageLedger,
    load_tariffs,
    open_ledger,
)

_EXIT_OK = 0
_EXIT_CHECK = 1
_EXIT_LIVE = 2
_ANSWER_DATE = "2026-09-12"
_MODE_NAMES = (
    "business-message",
    "meeting-action-items",
    "meeting-protocol",
    "text-summary",
    "text-translation",
)
_INTENTS = {
    "business-message": "Напиши деловое письмо клиенту",
    "meeting-action-items": "Выпиши задачи встречи",
    "meeting-protocol": "Составь протокол совещания",
    "text-summary": "Суммируй статью коротко",
    "text-translation": "Переведи текст на английский",
}
_MATERIAL = (
    "Анна: После паузы решили включить показатели продаж. "
    "Ответственный за отчёт не назван."
)
_PROTOCOL_MARKDOWN = f"""# Протокол встречи: Тема

**Дата:** {PLACEHOLDER}
**Участники:** Анна

## Обсуждение
- пункт

## Решения
- включить показатели продаж

## Задачи
- {PLACEHOLDER}

## Открытые вопросы
- вопрос
"""
_ZIP_MAGIC = b"PK"
_TARIFFS = _ROOT / "config" / "model-tariffs.yaml"


class DemoCheckError(Exception):
    """Сигнализирует, что офлайн-демонстрация не прошла встроенную проверку."""


@dataclass(frozen=True, slots=True)
class ModeSmoke:
    """Фиксирует исход одного режима офлайн-smoke.

    Attributes:
        name: ожидаемое имя режима.
        selected: фактически выбранное имя.
        outcome: машинный исход ассистента.
        text: текстовый ответ или его отсутствие.
    """

    name: str
    selected: str | None
    outcome: str
    text: str | None


@dataclass(frozen=True, slots=True)
class ProtocolDemo:
    """Фиксирует путь protocol → уточнение → финал → DOCX.

    Attributes:
        clarification_ids: идентификаторы построенных уточнений.
        final_date: дата после финализации.
        docx_bytes: байты собранного Word.
    """

    clarification_ids: tuple[str, ...]
    final_date: str
    docx_bytes: bytes


@dataclass(frozen=True, slots=True)
class UsageDemo:
    """Фиксирует журнал расходов и отказ по дневному потолку.

    Attributes:
        entry_count: число committed-строк успешного прогона.
        today_nanos: сумма committed за UTC-день.
        budget_blocked: отказ потолка до вызова модели.
    """

    entry_count: int
    today_nanos: int
    budget_blocked: bool


@dataclass(frozen=True, slots=True)
class DemoReport:
    """Собирает наблюдаемый результат офлайн-демонстрации.

    Attributes:
        modes: smoke пяти режимов.
        protocol: путь протокола или его отсутствие.
        usage: сводка расходов или её отсутствие.
        eval_text: текстовый отчёт офлайн-eval.
        eval_boundary_failed: число обходов границы.
    """

    modes: tuple[ModeSmoke, ...]
    protocol: ProtocolDemo | None
    usage: UsageDemo | None
    eval_text: str
    eval_boundary_failed: int


def verify_demo(report: DemoReport) -> None:
    """Проверяет, что офлайн-демонстрация закрывает договор S4.

    Args:
        report: собранный результат прогона.

    Raises:
        DemoCheckError: один из тезисов демонстрации не подтверждён.
    """
    _verify_modes(report.modes)
    _verify_protocol(report.protocol)
    _verify_usage(report.usage)
    _verify_eval(report.eval_text, report.eval_boundary_failed)


def run_offline() -> DemoReport:
    """Собирает офлайн-демонстрацию без живой модели.

    Returns:
        Наблюдаемый отчёт для печати и встроенной проверки.
    """
    snapshot = _snapshot()
    with TemporaryDirectory() as raw:
        return _collect_demo(snapshot, Path(raw))


def render_demo(report: DemoReport) -> str:
    """Формирует текстовую сводку офлайн-демонстрации.

    Args:
        report: собранный результат прогона.

    Returns:
        Человекочитаемый отчёт пяти режимов, протокола, расходов и eval.
    """
    return "\n".join(
        (
            "офлайн-демо: пять режимов, протокол и сводка eval",
            "",
            *_mode_lines(report.modes),
            "",
            *_protocol_lines(report.protocol),
            "",
            *_usage_lines(report.usage),
            "",
            "eval:",
            report.eval_text,
            "",
            "абсолютная защита от injection не обещается",
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Запускает офлайн-демо или отклоняет живой режим.

    Args:
        argv: аргументы командной строки без имени скрипта.

    Returns:
        Код выхода процесса.
    """
    if not _parse_args(argv).offline:
        _emit("Живое демо не является условием merge. Укажите --offline.")
        return _EXIT_LIVE
    report = run_offline()
    _emit(render_demo(report))
    try:
        verify_demo(report)
    except DemoCheckError as error:
        _emit(f"проверка демо: {error}")
        return _EXIT_CHECK
    return _EXIT_OK


def load_eval_runner() -> ModuleType:
    """Загружает офлайн-runner eval без добавления каталога в sys.path.

    Returns:
        Модуль `eval/run_eval.py`.
    """
    spec = importlib.util.spec_from_file_location(
        "skillhub_offline_eval",
        _ROOT / "eval" / "run_eval.py",
    )
    if spec is None or spec.loader is None:
        message = "не удалось загрузить eval/run_eval.py"
        raise DemoCheckError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_demo(snapshot: dict[str, Skill], folder: Path) -> DemoReport:
    catalog = load_tariffs(_TARIFFS)
    ledger = open_ledger(folder / "usage.sqlite3")
    modes = tuple(_run_mode(name, snapshot, ledger, catalog) for name in _MODE_NAMES)
    runner = load_eval_runner()
    eval_report = runner.evaluate_offline()
    return DemoReport(
        modes=modes,
        protocol=_protocol_demo(modes),
        usage=_usage_demo(ledger, catalog, folder),
        eval_text=runner.render_report(eval_report),
        eval_boundary_failed=eval_report.boundary_failed,
    )


def _snapshot() -> dict[str, Skill]:
    registry = SkillRegistry(_ROOT / "skills")
    registry.reload()
    return dict(registry.snapshot)


def _run_mode(
    name: str,
    snapshot: dict[str, Skill],
    ledger: UsageLedger,
    catalog: TariffCatalog,
) -> ModeSmoke:
    outcome = _assistant(name, ledger, catalog).run(_INTENTS[name], _MATERIAL, snapshot)
    return _mode_smoke(name, outcome)


def _assistant(
    name: str,
    ledger: UsageLedger,
    catalog: TariffCatalog,
) -> Assistant:
    classify = _metered(ledger, catalog, _classify_text(name))
    generate = _metered(ledger, catalog, _generate_text(name))
    return Assistant(
        SkillClassifier(classify),
        SkillHandlerRegistry(
            PromptSkillHandler(generate),
            extra={"meeting-protocol": ProtocolSkillHandler(generate)},
        ),
    )


def _metered(
    ledger: UsageLedger,
    catalog: TariffCatalog,
    text: str,
) -> MeteredLlmGateway:
    return MeteredLlmGateway(
        FakeLlmGateway(result=_llm_result(text)),
        ledger,
        catalog,
    )


def _classify_text(name: str) -> str:
    return f'{{"skill": "{name}"}}'


def _generate_text(name: str) -> str:
    if name == "meeting-protocol":
        return _PROTOCOL_MARKDOWN
    return f"DEMO-{name}"


def _llm_result(text: str) -> LlmResult:
    return LlmResult(
        text=text,
        finish_reason="stop",
        usage=LlmUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _mode_smoke(name: str, outcome: AssistantOutcome) -> ModeSmoke:
    return ModeSmoke(name, outcome.selected_skill, outcome.outcome, outcome.text)


def _protocol_demo(modes: tuple[ModeSmoke, ...]) -> ProtocolDemo | None:
    text = _protocol_text(modes)
    if text is None:
        return None
    protocol = parse(text)
    gaps = build_clarifications(protocol)
    final = finalize(protocol, _close_gaps(gaps), _MATERIAL)
    return ProtocolDemo(
        clarification_ids=tuple(item.id for item in gaps),
        final_date=final.protocol.date,
        docx_bytes=build_docx(final.protocol),
    )


def _protocol_text(modes: tuple[ModeSmoke, ...]) -> str | None:
    for item in modes:
        if item.name == "meeting-protocol":
            return item.text
    return None


def _close_gaps(gaps: tuple[Clarification, ...]) -> tuple[dict[str, str], ...]:
    return tuple(_decision(item) for item in gaps)


def _decision(item: Clarification) -> dict[str, str]:
    if item.id == "date":
        return {"id": "date", "action": "answer", "value": _ANSWER_DATE}
    return {"id": item.id, "action": "skip"}


def _usage_demo(
    ledger: UsageLedger,
    catalog: TariffCatalog,
    folder: Path,
) -> UsageDemo:
    committed, _reserved = ledger.day_totals(datetime.now(UTC).date())
    return UsageDemo(
        entry_count=len(ledger.list()),
        today_nanos=committed,
        budget_blocked=_budget_blocked(catalog, folder / "blocked.sqlite3"),
    )


def _budget_blocked(catalog: TariffCatalog, path: Path) -> bool:
    inner = FakeLlmGateway(result=_llm_result("не должен"))
    gateway = MeteredLlmGateway(inner, open_ledger(path), catalog, 0)
    try:
        gateway.complete(_probe_request())
    except DailyBudgetExceededError:
        return not inner.requests
    return False


def _probe_request() -> LlmRequest:
    return LlmRequest(messages=(LlmMessage(role="user", content="x"),))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Офлайн-демонстрация SkillHub.")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


def _emit(text: str) -> None:
    sys.stdout.buffer.write(f"{text}\n".encode())
    sys.stdout.buffer.flush()


def _verify_modes(modes: tuple[ModeSmoke, ...]) -> None:
    names = tuple(item.name for item in modes)
    if names != _MODE_NAMES:
        message = "smoke не покрыл пять канонических режимов"
        raise DemoCheckError(message)
    for item in modes:
        _verify_one_mode(item)


def _verify_one_mode(item: ModeSmoke) -> None:
    if item.selected != item.name or item.outcome != "success":
        message = f"режим {item.name} не дал успешный выбор"
        raise DemoCheckError(message)
    if not item.text:
        message = f"режим {item.name} вернул пустой текст"
        raise DemoCheckError(message)


def _verify_protocol(demo: ProtocolDemo | None) -> None:
    if demo is None:
        message = "путь protocol→DOCX не выполнен"
        raise DemoCheckError(message)
    _verify_protocol_values(demo)


def _verify_protocol_values(demo: ProtocolDemo) -> None:
    if "date" not in demo.clarification_ids:
        message = "черновик не построил уточнение даты"
        raise DemoCheckError(message)
    if demo.final_date != _ANSWER_DATE:
        message = "финализация не записала ответ в дату"
        raise DemoCheckError(message)
    _verify_docx(demo.docx_bytes)


def _verify_docx(payload: bytes) -> None:
    if not payload.startswith(_ZIP_MAGIC):
        message = "DOCX не собран как ZIP-пакет"
        raise DemoCheckError(message)
    with ZipFile(BytesIO(payload)) as archive:
        xml = archive.read("word/document.xml")
    if "Протокол встречи".encode() not in xml:
        message = "DOCX не содержит заголовок протокола"
        raise DemoCheckError(message)


def _verify_usage(demo: UsageDemo | None) -> None:
    if demo is None:
        message = "сводка расходов не собрана"
        raise DemoCheckError(message)
    if demo.entry_count <= 0 or demo.today_nanos <= 0:
        message = "журнал расходов пуст"
        raise DemoCheckError(message)
    if not demo.budget_blocked:
        message = "отказ дневного потолка не показан"
        raise DemoCheckError(message)


def _verify_eval(text: str, failed: int) -> None:
    if "границы:" not in text or "остаточный смысл:" not in text:
        message = "сводка eval не содержит секций границ"
        raise DemoCheckError(message)
    if "абсолютная защита" not in text:
        message = "сводка eval не фиксирует ограничение модели"
        raise DemoCheckError(message)
    if failed:
        message = "офлайн-eval зафиксировал обход границы"
        raise DemoCheckError(message)


def _mode_lines(modes: tuple[ModeSmoke, ...]) -> tuple[str, ...]:
    if not modes:
        return ("режимы: нет",)
    return (
        "режимы:",
        *(f"{item.name}: {item.outcome}" for item in modes),
    )


def _protocol_lines(demo: ProtocolDemo | None) -> tuple[str, ...]:
    if demo is None:
        return ("протокол: нет",)
    return (
        "протокол:",
        f"уточнения: {', '.join(demo.clarification_ids)}",
        f"финальная дата: {demo.final_date}",
        f"docx: {len(demo.docx_bytes)} байт",
    )


def _usage_lines(demo: UsageDemo | None) -> tuple[str, ...]:
    if demo is None:
        return ("расходы: нет",)
    return (
        "расходы:",
        f"записей: {demo.entry_count}",
        f"сегодня: {demo.today_nanos} нано-USD",
        f"потолок отклонил вызов: {demo.budget_blocked}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
