"""Серия baseline-прогонов для главы 2: один сценарий в режимах G0-G3."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

from sphere_lc.engine import WorldEngine, default_artifacts
from sphere_lc.governance_modes import BUILTIN_GOVERNANCE_MODES, apply_builtin_governance_mode
from sphere_lc.scenario import load_scenario

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Запустить baseline-серию по главе 2 для одного сценария в режимах G0-G3."
    )
    parser.add_argument(
        "--scenario",
        default="scenarios/procurement_tender_core_governance.json",
        help="Базовый сценарий для серии (по умолчанию procurement_tender_core_governance.json).",
    )
    parser.add_argument(
        "--out",
        default="results/chapter2_procurement_baseline",
        help="Корневая директория для серии прогонов.",
    )
    parser.add_argument(
        "--governance",
        nargs="+",
        default=list(BUILTIN_GOVERNANCE_MODES),
        help="Список built-in режимов управления для серии.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Сколько повторов выполнять для каждого governance-режима.",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=None,
        help="Опциональный override числа тиков.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _summarize_mode(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"runs": 0}

    numeric_fields = {
        "precision",
        "recall",
        "f1",
        "semantic_precision",
        "semantic_recall",
        "semantic_f1",
        "case_precision",
        "case_recall",
        "case_f1",
        "temporal_violations_total",
        "identity_machine_name_total",
        "identity_role_alias_total",
        "phantom_rejection_total",
        "bureaucratic_loop_total",
        "world_event_total",
        "narrating_leakage_total",
        "perform_approved_total",
        "reputation_event_total",
    }
    buckets: dict[str, list[float]] = {field: [] for field in numeric_fields}
    for run in runs:
        evaluation = run.get("evaluation") or {}
        fidelity = run.get("fidelity") or {}
        for field in numeric_fields:
            value = evaluation.get(field)
            if isinstance(value, (int, float)):
                buckets[field].append(float(value))
                continue
            value = fidelity.get(field)
            if isinstance(value, (int, float)):
                buckets[field].append(float(value))

    aggregated = {"runs": len(runs)}
    for field, values in sorted(buckets.items()):
        if values:
            aggregated[field] = round(mean(values), 4)
    return aggregated


async def _run_series(
    *,
    scenario_path: Path,
    out_dir: Path,
    governance_modes: list[str],
    repeats: int,
    ticks_override: int | None,
) -> dict[str, Any]:
    scenario_path = scenario_path.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in governance_modes}

    for mode in governance_modes:
        normalized = str(mode).strip().upper()
        if normalized not in BUILTIN_GOVERNANCE_MODES:
            raise ValueError(f"Unsupported built-in governance mode: {mode!r}")
        for run_index in range(max(1, int(repeats))):
            cfg = load_scenario(scenario_path)
            apply_builtin_governance_mode(cfg, normalized)
            if ticks_override is not None:
                cfg.ticks = int(ticks_override)

            run_name = f"{scenario_path.stem}_{normalized}_run{run_index + 1}"
            run_dir = out_dir / run_name
            artifacts = default_artifacts(run_dir)
            await WorldEngine(cfg=cfg, artifacts=artifacts).run()

            evaluation = _load_json(run_dir / "evaluation.json") or {}
            fidelity = _load_json(run_dir / "fidelity.json") or {}
            summary = _load_json(run_dir / "summary.json") or {}
            item = {
                "run_name": run_name,
                "governance": normalized,
                "run_index": run_index + 1,
                "out_dir": str(run_dir),
                "evaluation": evaluation,
                "fidelity": fidelity,
                "summary": summary,
            }
            results.append(item)
            by_mode[normalized].append(item)

    report = {
        "scenario": str(scenario_path),
        "repeats": max(1, int(repeats)),
        "governance_modes": governance_modes,
        "runs": results,
        "by_mode": {
            mode: _summarize_mode(items)
            for mode, items in sorted(by_mode.items())
        },
    }
    report_path = out_dir / "comparative_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        _run_series(
            scenario_path=Path(args.scenario),
            out_dir=Path(args.out),
            governance_modes=[str(mode).strip().upper() for mode in args.governance],
            repeats=int(args.repeats),
            ticks_override=args.ticks,
        )
    )
    print(
        json.dumps(
            {
                "scenario": report["scenario"],
                "out_dir": args.out,
                "modes": report["governance_modes"],
                "repeats": report["repeats"],
                "report": str(Path(args.out).resolve() / "comparative_report.json"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
