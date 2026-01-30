from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from magistry_sim.engine import SimulationEngine
from magistry_sim.enums import GovernanceMode, ScenarioId
from magistry_sim.metrics import compute_metrics
from magistry_sim.models import GovernanceConfig
from magistry_sim.scenarios import SCENARIOS


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="magistry-sim", description="AI+DAO governance simulation")
    parser.add_argument("--list-scenarios", action="store_true", help="Показать доступные сценарии и выйти")
    parser.add_argument("--scenario", type=str, default="S1", help="ID сценария: S0..S9")
    parser.add_argument("--governance", type=str, default="G3", help="Режим управления: G0..G3")
    parser.add_argument("--seed", type=int, default=None, help="Seed (по умолчанию из сценария)")
    parser.add_argument("--ticks", type=int, default=None, help="Переопределить число тиков")
    parser.add_argument("--jsonl", type=str, default=None, help="Путь для JSONL логов (events)")
    parser.add_argument("--summary-json", type=str, default=None, help="Путь для JSON summary результата")
    return parser.parse_args()


def main() -> None:
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

    scenario_id = ScenarioId(args.scenario)
    governance_mode = GovernanceMode(args.governance)

    config = GovernanceConfig(mode=governance_mode)
    engine = SimulationEngine(governance=config)
    result = engine.run(scenario_id=scenario_id, seed=args.seed, ticks=args.ticks)
    metrics = compute_metrics(result)

    table = Table(title=f"Результат: {scenario_id.value} / {governance_mode.value}")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_row("ticks", str(metrics.ticks))
    table.add_row("corruption_rate", f"{metrics.corruption_rate:.2f}")
    table.add_row("audit_flag_rate", f"{metrics.audit_flag_rate:.2f}")
    table.add_row("tribunal_trigger_rate", f"{metrics.tribunal_trigger_rate:.2f}")
    table.add_row("tribunal_guilty_rate", f"{metrics.tribunal_guilty_rate:.2f}")
    console.print(table)

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

