"""CLI интерфейс для MAGISTRY-SIM."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.table import Table

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.llm import create_provider
from magistry_sim.metrics import Metrics, compute_metrics
from magistry_sim.models import GovernanceConfig
from magistry_sim.scenarios import SCENARIOS


def _parse_args() -> argparse.Namespace:
    """Парсинг аргументов CLI."""
    parser = argparse.ArgumentParser(prog="magistry-sim", description="AI+DAO governance simulation")
    parser.add_argument("--list-scenarios", action="store_true", help="Показать доступные сценарии и выйти")
    parser.add_argument("--scenario", type=str, default="S1", help="ID сценария: S0..S9")
    parser.add_argument("--governance", type=str, default="G3", help="Режим управления: G0..G3")
    parser.add_argument("--seed", type=int, default=None, help="Seed (по умолчанию из сценария)")
    parser.add_argument("--ticks", type=int, default=None, help="Переопределить число тиков")
    parser.add_argument("--mock", action="store_true", default=True, help="Использовать mock LLM (по умолчанию)")
    parser.add_argument("--no-mock", action="store_true", help="Использовать реальный LLM")
    parser.add_argument("--batch", action="store_true", help="Пакетный запуск S0-S9 × G0-G3")
    parser.add_argument("--jsonl", type=str, default=None, help="Путь для JSONL логов (events)")
    parser.add_argument("--summary-json", type=str, default=None, help="Путь для JSON summary результата")
    parser.add_argument("--viz", type=str, default=None, help="Директория для визуализаций")
    return parser.parse_args()


def _print_metrics_table(console: Console, title: str, metrics: Metrics) -> None:
    """Вывести таблицу метрик."""
    table = Table(title=title)
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_row("ticks", str(metrics.ticks))
    table.add_row("corruption_rate", f"{metrics.corruption_rate:.2f}")
    table.add_row("audit_flag_rate", f"{metrics.audit_flag_rate:.2f}")
    table.add_row("tribunal_trigger_rate", f"{metrics.tribunal_trigger_rate:.2f}")
    table.add_row("tribunal_guilty_rate", f"{metrics.tribunal_guilty_rate:.2f}")
    table.add_row("precision", f"{metrics.precision:.3f}")
    table.add_row("recall", f"{metrics.recall:.3f}")
    table.add_row("F1", f"{metrics.f1:.3f}")
    table.add_row("TP / FP / TN / FN",
                   f"{metrics.confusion.tp} / {metrics.confusion.fp} / "
                   f"{metrics.confusion.tn} / {metrics.confusion.fn}")
    console.print(table)


def _print_batch_table(
    console: Console,
    results: list[tuple[ScenarioId, GovernanceMode, Metrics]],
) -> None:
    """Вывести сводную таблицу пакетного запуска."""
    table = Table(title="Batch Results: S0-S9 × G0-G3")
    table.add_column("Scenario", style="bold")
    table.add_column("Governance")
    table.add_column("Ticks", justify="right")
    table.add_column("Corruption", justify="right")
    table.add_column("Flagged", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("F1", justify="right")
    table.add_column("TP/FP/TN/FN", justify="right")

    for sid, mode, m in results:
        table.add_row(
            sid.value, mode.value,
            str(m.ticks),
            f"{m.corruption_rate:.2f}",
            f"{m.audit_flag_rate:.2f}",
            f"{m.precision:.2f}",
            f"{m.recall:.2f}",
            f"{m.f1:.2f}",
            f"{m.confusion.tp}/{m.confusion.fp}/{m.confusion.tn}/{m.confusion.fn}",
        )
    console.print(table)


async def _run_batch(provider: object, console: Console) -> list[tuple[ScenarioId, GovernanceMode, Metrics]]:
    """Пакетный запуск S0-S9 × G0-G3."""
    results: list[tuple[ScenarioId, GovernanceMode, Metrics]] = []
    for scenario_id in ScenarioId:
        for mode in GovernanceMode:
            config = GovernanceConfig(mode=mode)
            engine = SimulationEngine(governance=config, llm=provider)  # type: ignore[arg-type]
            result = await engine.run(scenario_id=scenario_id)
            metrics = compute_metrics(result)
            results.append((scenario_id, mode, metrics))
    return results


def main() -> None:
    """Точка входа CLI."""
    args = _parse_args()
    console = Console()

    if args.list_scenarios:
        table = Table(title="Сценарии")
        table.add_column("ID", style="bold")
        table.add_column("Название")
        table.add_column("Тики", justify="right")
        table.add_column("Описание")
        for sid, s in SCENARIOS.items():
            table.add_row(sid.value, s.title, str(s.ticks), s.description)
        console.print(table)
        return

    use_mock = args.mock and not args.no_mock
    provider = create_provider(mock=use_mock)

    if args.batch:
        batch_results = asyncio.run(_run_batch(provider, console))
        _print_batch_table(console, batch_results)

        if args.summary_json:
            path = Path(args.summary_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = [
                {
                    "scenario": sid.value,
                    "governance": mode.value,
                    "metrics": {
                        "ticks": m.ticks,
                        "corruption_rate": m.corruption_rate,
                        "audit_flag_rate": m.audit_flag_rate,
                        "precision": m.precision,
                        "recall": m.recall,
                        "f1": m.f1,
                        "tp": m.confusion.tp,
                        "fp": m.confusion.fp,
                        "tn": m.confusion.tn,
                        "fn": m.confusion.fn,
                    },
                }
                for sid, mode, m in batch_results
            ]
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            console.print(f"[green]Wrote batch summary[/green]: {path}")

        if args.viz:
            from magistry_sim.viz import (
                plot_confusion_heatmap,
                plot_governance_comparison,
            )
            viz_dir = Path(args.viz)
            viz_dir.mkdir(parents=True, exist_ok=True)
            plot_governance_comparison(batch_results, viz_dir / "g_comparison.png")
            plot_confusion_heatmap(batch_results, viz_dir / "confusion_heatmap.png")
            console.print(f"[green]Wrote visualizations[/green]: {viz_dir}")

        return

    scenario_id = ScenarioId(args.scenario)
    governance_mode = GovernanceMode(args.governance)

    config = GovernanceConfig(mode=governance_mode)
    engine = SimulationEngine(governance=config, llm=provider)
    result = asyncio.run(engine.run(scenario_id=scenario_id, seed=args.seed, ticks=args.ticks))
    metrics = compute_metrics(result)

    _print_metrics_table(console, f"Результат: {scenario_id.value} / {governance_mode.value}", metrics)

    if args.jsonl:
        path = Path(args.jsonl)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for event in result.events:
                f.write(event.model_dump_json())
                f.write("\n")
        console.print(f"[green]Wrote events[/green]: {path}")

    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(result.model_dump_json())
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        console.print(f"[green]Wrote summary[/green]: {path}")

    if args.viz:
        from magistry_sim.viz import (
            plot_corruption_timeline,
            plot_reputation_dynamics,
        )
        viz_dir = Path(args.viz)
        viz_dir.mkdir(parents=True, exist_ok=True)
        plot_corruption_timeline(result, viz_dir / "timeline.png")
        plot_reputation_dynamics(result, viz_dir / "reputation.png")
        console.print(f"[green]Wrote visualizations[/green]: {viz_dir}")
