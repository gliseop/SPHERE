"""Предобогащение персон сценария для воспроизводимых серий главы 3.

Скрипт принимает сценарий, обогащает все персоны через
``WorldEngine._enrich_personas`` и сохраняет результат в JSON в формате,
совместимом с ``WorldEngine._save_personas_cache``. Этот файл затем
подаётся в ``RuntimeConfig.personas_pre_enriched_path``, что гарантирует
идентичность персон между прогонами G0, G1, G2, G3 одной серии.

Симуляция не запускается: после обогащения скрипт сразу пишет файл и
выходит. Артефакты прогона (events.jsonl, trace.jsonl и пр.) пишутся
во временную директорию и затем удаляются.

Пример использования::

    python scripts/pregenerate_personas.py \\
        --scenario scenarios/procurement_tender_large.json \\
        --out personas_chapter3/procurement_tender_large.personas.json \\
        --mode full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.events import EventLog
from sphere_lc.llm import LLMCaller, create_llm_provider
from sphere_lc.scenario import load_scenario
from sphere_lc.tracing import TraceLog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("sphere_lc.pregenerate_personas")


def _parse_args() -> argparse.Namespace:
    """Разобрать аргументы командной строки.

    Returns:
        ``argparse.Namespace`` со следующими полями:
            - ``scenario``: путь к файлу сценария (JSON или YAML);
            - ``out``: путь для сохранения JSON с персонами;
            - ``mode``: режим обогащения (``full`` или ``core``);
            - ``ticks``: служебный параметр, не используется.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Предобогатить персон сценария и сохранить их в JSON для "
            "последующего использования в серии прогонов главы 3."
        )
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Путь к файлу сценария (JSON/YAML).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Куда сохранить итоговый JSON с предобогащёнными персонами.",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "core"),
        default="full",
        help="Режим обогащения персон (по умолчанию full).",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=0,
        help=(
            "Служебный параметр, в этом скрипте не используется: "
            "симуляция не запускается."
        ),
    )
    return parser.parse_args()


async def _pregenerate(
    *,
    scenario_path: Path,
    out_path: Path,
    mode: str,
) -> dict[str, Any]:
    """Выполнить обогащение и сохранить файл.

    Args:
        scenario_path: Путь к сценарию.
        out_path: Путь к итоговому JSON-файлу.
        mode: Режим обогащения, ``full`` или ``core``.

    Returns:
        Краткий отчёт со списком agent_id и контрольным fingerprint.
    """
    cfg = load_scenario(scenario_path)
    cfg.runtime.enrich_personas = True
    cfg.runtime.persona_enrich_mode = mode
    # Игнорируем уже указанный путь — иначе обогащения не произойдёт,
    # потому что персоны будут просто загружены из существующего файла.
    cfg.runtime.personas_pre_enriched_path = None

    with tempfile.TemporaryDirectory(prefix="sphere_pregen_") as tmp_root:
        tmp_dir = Path(tmp_root)
        artifacts = RunArtifacts(
            out_dir=tmp_dir,
            events_path=tmp_dir / "events.jsonl",
            trace_path=tmp_dir / "trace.jsonl",
        )
        engine = WorldEngine(cfg=cfg, artifacts=artifacts)

        provider = create_llm_provider(cfg.llm)
        trace = TraceLog(artifacts.trace_path, max_chars=cfg.llm.trace_max_chars)
        llm = LLMCaller(provider=provider, trace=trace)

        event_log = EventLog(artifacts.events_path)
        state = engine._init_state(event_log=event_log)
        await engine._enrich_personas(state=state, llm=llm)

        cache_input = engine._personas_cache_input(state=state)
        fingerprint = engine._personas_cache_fingerprint(cache_input)
        personas = {aid: agent.persona for aid, agent in sorted(state.agents.items())}

    payload = {
        "meta": {
            "version": 1,
            "fingerprint": fingerprint,
            "input": cache_input,
            "source": "pregenerate_personas",
            "scenario_path": str(scenario_path.resolve()),
            "mode": mode,
        },
        "personas": {
            aid: persona.model_dump(mode="json") for aid, persona in personas.items()
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "fingerprint": fingerprint,
        "agents": list(personas.keys()),
        "out": str(out_path.resolve()),
    }


def main() -> None:
    """Точка входа CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    args = _parse_args()
    scenario_path = Path(args.scenario).resolve()
    if not scenario_path.exists():
        raise SystemExit(f"Сценарий не найден: {scenario_path}")
    out_path = Path(args.out).resolve()
    report = asyncio.run(
        _pregenerate(scenario_path=scenario_path, out_path=out_path, mode=args.mode)
    )
    print(
        f"обогащено {len(report['agents'])} персон, сохранено в {report['out']}"
    )
    print(
        json.dumps(
            {
                "scenario": str(scenario_path),
                "mode": args.mode,
                "out": report["out"],
                "fingerprint": report["fingerprint"],
                "agents": report["agents"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
