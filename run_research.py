"""Полноценное исследование: прогон сценариев S0–S2 через режимы G0–G3.

Использует CrewAI + MiniMax-M2.5. Результаты сохраняются в results/.
"""

import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

os.environ["CREWAI_TRACING_ENABLED"] = "false"

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.table import Table

from magistry_sim.agents import CrewAIAgentRunner
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.environment import Environment
from magistry_sim.metrics import compute_metrics
from magistry_sim.scenarios import get_scenario

console = Console(file=sys.stdout)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

RUNS = [
    (ScenarioId.S0, GovernanceMode.G0),
    (ScenarioId.S0, GovernanceMode.G1),
    (ScenarioId.S0, GovernanceMode.G2),
    (ScenarioId.S0, GovernanceMode.G3),
    (ScenarioId.S1, GovernanceMode.G0),
    (ScenarioId.S1, GovernanceMode.G1),
    (ScenarioId.S1, GovernanceMode.G2),
    (ScenarioId.S1, GovernanceMode.G3),
    (ScenarioId.S2, GovernanceMode.G0),
    (ScenarioId.S2, GovernanceMode.G1),
    (ScenarioId.S2, GovernanceMode.G2),
    (ScenarioId.S2, GovernanceMode.G3),
]


def run_single(
    scenario_id: ScenarioId,
    governance: GovernanceMode,
    runner: CrewAIAgentRunner,
    seed: int = 42,
) -> dict:
    """Выполнить один прогон и вернуть результаты.

    Args:
        scenario_id: Идентификатор сценария.
        governance: Режим управления.
        runner: AgentRunner.
        seed: Зерно.

    Returns:
        Словарь с результатами и метриками.
    """
    scenario = get_scenario(scenario_id)
    env = Environment(
        scenario=scenario,
        governance=governance,
        runner=runner,
        seed=seed,
    )

    start = time.time()
    result = env.run()
    elapsed = time.time() - start

    metrics = compute_metrics(result)

    # Сохранить журнал событий
    events_path = (
        RESULTS_DIR
        / f"{scenario_id.value}_{governance.value}_events.jsonl"
    )
    env.state.event_log.save_jsonl(events_path)

    return {
        "scenario": scenario_id.value,
        "governance": governance.value,
        "seed": seed,
        "elapsed_seconds": round(elapsed, 1),
        "rounds": result.rounds_completed,
        "total_cases": metrics.total_cases,
        "cases_by_type": metrics.cases_by_type,
        "violations_total": metrics.violations_total,
        "violations_detected": metrics.violations_detected,
        "precision": metrics.confusion.precision,
        "recall": metrics.confusion.recall,
        "f1": metrics.confusion.f1,
        "tp": metrics.confusion.tp,
        "fp": metrics.confusion.fp,
        "tn": metrics.confusion.tn,
        "fn": metrics.confusion.fn,
        "private_message_ratio": metrics.private_message_ratio,
        "reputation": result.final_reputation,
        "cases": result.cases,
        "messages_count": len(result.messages),
    }


def main():
    """Запуск исследования."""
    console.rule("[bold]Исследование MAGISTRY v3[/bold]")
    console.print(
        f"Сценарии: S0–S2, Режимы: G0–G3, "
        f"Всего прогонов: {len(RUNS)}"
    )
    console.print()

    runner = CrewAIAgentRunner(verbose=False)
    all_results = []

    for i, (sid, gov) in enumerate(RUNS):
        label = f"{sid.value}/{gov.value}"
        console.print(
            f"[bold][{i+1}/{len(RUNS)}] {label}[/bold] ...",
            end=" ",
        )

        try:
            result = run_single(sid, gov, runner)
            all_results.append(result)
            console.print(
                f"[green]OK[/green] "
                f"({result['elapsed_seconds']}s, "
                f"дел: {result['total_cases']}, "
                f"сообщений: {result['messages_count']})"
            )
        except Exception as exc:
            console.print(f"[red]ОШИБКА: {exc}[/red]")
            all_results.append({
                "scenario": sid.value,
                "governance": gov.value,
                "error": str(exc),
            })

    # Сохранить общий отчёт
    summary_path = RESULTS_DIR / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    console.print()
    console.rule("[bold]Сводные результаты[/bold]")

    # Таблица результатов
    table = Table(title="Результаты исследования")
    table.add_column("Сценарий")
    table.add_column("Режим")
    table.add_column("Дел", justify="right")
    table.add_column("Сообщений", justify="right")
    table.add_column("Нарушений", justify="right")
    table.add_column("Обнаружено", justify="right")
    table.add_column("TP/FP/TN/FN", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("F1", justify="right")
    table.add_column("Время", justify="right")

    for r in all_results:
        if "error" in r:
            table.add_row(
                r["scenario"], r["governance"],
                "-", "-", "-", "-", "-", "-", "-", "-",
                f"[red]{r['error'][:30]}[/red]",
            )
            continue
        table.add_row(
            r["scenario"],
            r["governance"],
            str(r["total_cases"]),
            str(r["messages_count"]),
            str(r["violations_total"]),
            str(r["violations_detected"]),
            f"{r['tp']}/{r['fp']}/{r['tn']}/{r['fn']}",
            f"{r['precision']:.2f}",
            f"{r['recall']:.2f}",
            f"{r['f1']:.2f}",
            f"{r['elapsed_seconds']}s",
        )

    console.print(table)
    console.print(f"\nОтчёт сохранён: {summary_path}")
    console.print(
        f"Журналы событий: {RESULTS_DIR}/*_events.jsonl"
    )


if __name__ == "__main__":
    main()
