"""CLI для SPHERE-LC."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, MofNCompleteColumn

from .composer import WorldComposer
from .config import LLMConfig
from .engine import WorldEngine, TickProgress, default_artifacts
from .governance_modes import BUILTIN_GOVERNANCE_MODES, apply_builtin_governance_mode
from .llm import LLMCaller, create_llm_provider
from .oracle import ViolationOracle, save_violations
from .scenario import load_scenario
from .tracing import TraceLog


console = Console(file=sys.stdout, force_terminal=False)


class _SafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser, устойчивый к несовместимой кодировке stdout/stderr."""

    def _print_message(self, message: str, file: TextIO | None = None) -> None:
        if not message:
            return
        target = sys.stderr if file is None else file
        try:
            target.write(message)
        except UnicodeEncodeError:
            encoding = getattr(target, "encoding", None) or "utf-8"
            buffer = getattr(target, "buffer", None)
            if buffer is not None:
                buffer.write(message.encode(encoding, errors="replace"))
                return
            safe_message = message.encode(encoding, errors="replace").decode(
                encoding, errors="replace"
            )
            target.write(safe_message)


def _cmd_run(args: argparse.Namespace) -> None:
    cfg = load_scenario(args.scenario)
    if args.ticks is not None:
        cfg.ticks = int(args.ticks)
    if bool(getattr(args, "enrich_personas", False)):
        cfg.runtime.enrich_personas = True
    if getattr(args, "persona_enrich_mode", None):
        cfg.runtime.persona_enrich_mode = str(args.persona_enrich_mode)
    if getattr(args, "governance", None):
        apply_builtin_governance_mode(cfg, args.governance)
    out_dir = Path(args.out) if args.out else Path("results") / datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts = default_artifacts(out_dir)

    console.print(f"[bold]SPHERE-LC run[/bold] scenario={args.scenario} ticks={cfg.ticks} out={out_dir}")

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[stats]}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Simulating", total=cfg.ticks, stats="")

        def _on_tick(tp: TickProgress) -> None:
            parts = [f"{tp.events_count} ev", f"{tp.agents_acted} agents"]
            if tp.audit_events_count:
                parts.append(f"{tp.audit_events_count} audit")
            if tp.truth_records_count:
                parts.append(f"{tp.truth_records_count} truth")
            stats = ", ".join(parts)
            if tp.simulated_date:
                stats += f"  [{tp.simulated_date}]"
            progress.update(task_id, completed=tp.tick + 1, stats=stats)

        engine = WorldEngine(cfg=cfg, artifacts=artifacts)
        engine.on_tick_done = _on_tick
        asyncio.run(engine.run())

    console.print(
        f"[green]Done[/green] events={artifacts.events_path} trace={artifacts.trace_path} "
        f"truth={artifacts.truth_path} evaluation={artifacts.evaluation_path}"
    )


def _llm_config_from_args(args: argparse.Namespace) -> LLMConfig:
    cfg = LLMConfig()
    if getattr(args, "model", None):
        cfg.model = args.model
    if getattr(args, "base_url", None):
        cfg.base_url = args.base_url
    if getattr(args, "provider_order", None):
        cfg.provider_order = [p.strip() for p in args.provider_order.split(",") if p.strip()]
    if getattr(args, "temperature", None) is not None:
        cfg.temperature = float(args.temperature)
    return cfg


def _cmd_compose(args: argparse.Namespace) -> None:
    llm_cfg = _llm_config_from_args(args)
    provider = create_llm_provider(llm_cfg)
    trace_path = Path(args.trace) if args.trace else Path("results") / "compose_trace.jsonl"
    llm = LLMCaller(provider=provider, trace=TraceLog(trace_path, max_chars=llm_cfg.trace_max_chars))

    description = args.description
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8")
    composer = WorldComposer(llm=llm, temperature=llm_cfg.temperature)
    cfg = asyncio.run(
        composer.compose(
            description=description,
            ticks=int(args.ticks),
            language=args.language,
        )
    )
    cfg.llm = llm_cfg
    out_path = Path(args.out)
    from .scenario import save_scenario

    save_scenario(cfg, out_path)
    console.print(f"[green]Composed[/green] scenario={out_path}")


def _cmd_oracle(args: argparse.Namespace) -> None:
    llm_cfg = _llm_config_from_args(args)
    provider = create_llm_provider(llm_cfg)
    trace_path = Path(args.trace) if args.trace else Path(args.out).with_suffix(".trace.jsonl")
    llm = LLMCaller(provider=provider, trace=TraceLog(trace_path, max_chars=llm_cfg.trace_max_chars))

    oracle = ViolationOracle(llm=llm, window_ticks=int(args.window), temperature=llm_cfg.temperature)
    violations = asyncio.run(oracle.analyze_events(events_path=Path(args.events)))
    save_violations(violations, Path(args.out))
    console.print(f"[green]Oracle done[/green] violations={len(violations)} out={args.out}")


def build_parser() -> argparse.ArgumentParser:
    """Построить parser CLI."""
    parser = _SafeArgumentParser(
        description="SPHERE-LC: greenfield-движок на LangChain/LangGraph"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Запустить симуляцию по YAML/JSON сценарию")
    p_run.add_argument("--scenario", type=str, required=True, help="Путь к сценарию (.yaml/.json)")
    p_run.add_argument("--out", type=str, default=None, help="Выходная директория (по умолчанию results/<ts>)")
    p_run.add_argument("--ticks", type=int, default=None, help="Переопределить число тиков")
    p_run.add_argument(
        "--enrich-personas",
        action="store_true",
        help="Включить runtime-обогащение персон перед первым тиком",
    )
    p_run.add_argument(
        "--persona-enrich-mode",
        type=str,
        choices=("full", "core"),
        default=None,
        help="Режим обогащения персон: full (summary+biography+interview) или core (summary+biography)",
    )
    p_run.add_argument(
        "--governance",
        type=str,
        choices=list(BUILTIN_GOVERNANCE_MODES),
        default=None,
        help="Применить built-in governance mode (G0, G1, G2, G3)",
    )
    p_run.set_defaults(fn=_cmd_run)

    p_comp = sub.add_parser("compose", help="Сгенерировать сценарий из текстового описания (LLM)")
    p_comp.add_argument("--description", type=str, default="", help="Описание сценария (строкой)")
    p_comp.add_argument("--description-file", type=str, default=None, help="Путь к файлу с описанием")
    p_comp.add_argument("--out", type=str, required=True, help="Куда сохранить сценарий (.yaml/.json)")
    p_comp.add_argument("--ticks", type=int, default=25)
    p_comp.add_argument("--language", type=str, default="ru")
    p_comp.add_argument("--model", type=str, default=None)
    p_comp.add_argument("--base-url", type=str, default=None)
    p_comp.add_argument("--provider-order", type=str, default=None, help="Напр. Groq,OpenAI")
    p_comp.add_argument("--temperature", type=float, default=None)
    p_comp.add_argument("--trace", type=str, default=None, help="JSONL trace path")
    p_comp.set_defaults(fn=_cmd_compose)

    p_oracle = sub.add_parser("oracle", help="Пост-фактум анализ events.jsonl чанками (LLM)")
    p_oracle.add_argument("--events", type=str, required=True, help="Путь к events.jsonl")
    p_oracle.add_argument("--out", type=str, required=True, help="Выходной JSON с нарушениями")
    p_oracle.add_argument("--window", type=int, default=5, help="Размер окна в тиках")
    p_oracle.add_argument("--model", type=str, default=None)
    p_oracle.add_argument("--base-url", type=str, default=None)
    p_oracle.add_argument("--provider-order", type=str, default=None)
    p_oracle.add_argument("--temperature", type=float, default=None)
    p_oracle.add_argument("--trace", type=str, default=None, help="JSONL trace path")
    p_oracle.set_defaults(fn=_cmd_oracle)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Точка входа CLI."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.fn(args)


if __name__ == "__main__":
    main()
