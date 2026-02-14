"""Интерфейс командной строки MAGISTRY-SIM."""

from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

from .agents import MockAgentRunner
from .enums import GovernanceMode, ScenarioId
from .environment import Environment
from .metrics import compute_metrics
from .scenarios import SCENARIOS, get_scenario


console = Console(file=sys.stdout, force_terminal=False)


def _list_scenarios() -> None:
    """Вывести таблицу доступных сценариев."""
    table = Table(title="Доступные сценарии")
    table.add_column("ID", style="bold")
    table.add_column("Название")
    table.add_column("Описание")
    table.add_column("Раундов", justify="right")
    table.add_column("Агентов", justify="right")

    for sid, cfg in SCENARIOS.items():
        table.add_row(
            sid.value,
            cfg.title,
            cfg.description[:60] + "..."
            if len(cfg.description) > 60
            else cfg.description,
            str(cfg.max_rounds),
            str(len(cfg.agents)),
        )

    console.print(table)


def _print_result(result, metrics) -> None:
    """Вывести результаты симуляции.

    Args:
        result: Результат симуляции.
        metrics: Метрики симуляции.
    """
    console.print()
    console.rule("[bold]Результаты симуляции[/bold]")

    # Основные параметры
    info_table = Table(show_header=False)
    info_table.add_column("Параметр", style="bold")
    info_table.add_column("Значение")
    info_table.add_row("Сценарий", result.scenario_id)
    info_table.add_row("Режим управления", result.governance)
    info_table.add_row("Зерно", str(result.seed))
    info_table.add_row("Раундов", str(result.rounds_completed))
    info_table.add_row("Всего дел", str(metrics.total_cases))
    console.print(info_table)

    # Дела по типам
    if metrics.cases_by_type:
        type_table = Table(title="Дела по типам")
        type_table.add_column("Тип")
        type_table.add_column("Количество", justify="right")
        for ct, count in metrics.cases_by_type.items():
            type_table.add_row(ct, str(count))
        console.print(type_table)

    # Матрица ошибок
    cm = metrics.confusion
    cm_table = Table(title="Матрица ошибок")
    cm_table.add_column("Показатель")
    cm_table.add_column("Значение", justify="right")
    cm_table.add_row("Истинные обнаружения (TP)", str(cm.tp))
    cm_table.add_row("Ложные тревоги (FP)", str(cm.fp))
    cm_table.add_row("Истинные пропуски (TN)", str(cm.tn))
    cm_table.add_row("Пропущенные нарушения (FN)", str(cm.fn))
    cm_table.add_row("Точность (Precision)", f"{cm.precision:.2f}")
    cm_table.add_row("Полнота (Recall)", f"{cm.recall:.2f}")
    cm_table.add_row("F1-мера", f"{cm.f1:.2f}")
    console.print(cm_table)

    # Репутация
    if result.final_reputation:
        rep_table = Table(title="Итоговая репутация")
        rep_table.add_column("Агент")
        rep_table.add_column("Оценка", justify="right")
        rep_table.add_column("Заморожен")
        for aid, rep in result.final_reputation.items():
            rep_table.add_row(
                aid,
                f"{rep['score']:.1f}",
                "да" if rep["frozen"] else "нет",
            )
        console.print(rep_table)

    # Дела
    if result.cases:
        cases_table = Table(title="Дела")
        cases_table.add_column("ID")
        cases_table.add_column("Тип")
        cases_table.add_column("Название")
        cases_table.add_column("Стадия")
        cases_table.add_column("Решение")
        for cid, case in result.cases.items():
            cases_table.add_row(
                cid,
                case.get("case_type", ""),
                case.get("title", "")[:30],
                case.get("stage", ""),
                (case.get("decision") or "-")[:40],
            )
        console.print(cases_table)

    console.print()


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="MAGISTRY-SIM: симуляция организационных процессов"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="S0",
        help="Идентификатор сценария (S0–S6)",
    )
    parser.add_argument(
        "--governance",
        type=str,
        default=None,
        help="Режим управления (G0–G3)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Зерно генератора случайных чисел",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Количество раундов",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Использовать mock-runner (по умолчанию)",
    )
    parser.add_argument(
        "--no-mock",
        action="store_true",
        default=False,
        help="Использовать CrewAI-runner",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="Показать список сценариев",
    )
    parser.add_argument(
        "--jsonl",
        type=str,
        default=None,
        help="Сохранить журнал событий в JSONL",
    )
    parser.add_argument(
        "--summary-json",
        type=str,
        default=None,
        help="Сохранить метрики в JSON",
    )

    args = parser.parse_args()

    if args.list_scenarios:
        _list_scenarios()
        return

    try:
        scenario_id = ScenarioId(args.scenario)
    except ValueError:
        console.print(
            f"[red]Неизвестный сценарий: {args.scenario}[/red]"
        )
        sys.exit(1)

    scenario = get_scenario(scenario_id)

    if args.rounds is not None:
        scenario = scenario.model_copy(
            update={"max_rounds": args.rounds}
        )

    governance = None
    if args.governance:
        try:
            governance = GovernanceMode(args.governance)
        except ValueError:
            console.print(
                f"[red]Неизвестный режим: {args.governance}[/red]"
            )
            sys.exit(1)

    use_mock = not args.no_mock
    runner = MockAgentRunner() if use_mock else None

    if not use_mock:
        try:
            from .agents import CrewAIAgentRunner
            from .llm import create_provider

            provider = create_provider(mock=False)
            runner = CrewAIAgentRunner(provider)
        except ImportError:
            console.print(
                "[yellow]CrewAI недоступен, "
                "используется mock-runner[/yellow]"
            )
            runner = MockAgentRunner()

    env = Environment(
        scenario=scenario,
        governance=governance,
        runner=runner,
        seed=args.seed,
    )

    console.print(
        f"[bold]Запуск: {scenario.title} "
        f"({governance or scenario.governance.mode})[/bold]"
    )

    result = env.run()
    metrics = compute_metrics(result)

    _print_result(result, metrics)

    if args.jsonl:
        from pathlib import Path

        env.state.event_log.save_jsonl(Path(args.jsonl))
        console.print(f"Журнал сохранён: {args.jsonl}")

    if args.summary_json:
        from dataclasses import asdict

        with open(args.summary_json, "w", encoding="utf-8") as f:
            json.dump(
                asdict(metrics), f, ensure_ascii=False, indent=2
            )
        console.print(f"Метрики сохранены: {args.summary_json}")


if __name__ == "__main__":
    main()
