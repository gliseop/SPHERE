from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sphere_lc.config import ScenarioConfig
from sphere_lc.engine import RunArtifacts, WorldEngine
from sphere_lc.governance_modes import apply_builtin_governance_mode
from sphere_lc.llm import MockLLMProvider, StructuredLLMResponse


class _SelfNominationProvider(MockLLMProvider):
    """Mock: агент каждый тик пытается вынести себя на голосование."""

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        lowered = user.casefold()
        if "сегодняшний рабочий день:" in lowered and "ты — off 1." in lowered:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "nominate_position_change",
                            "target_agent_id": "agent:off_1",
                            "new_title": "chief_specialist",
                            "reason": "считаю себя лучшим кандидатом",
                        }
                    ]
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


def _mk_cfg() -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "governance-progression",
            "description": "Внутренний агент систематически инициирует самономинацию.",
            "ticks": 4,
            "runtime": {
                "max_actions_per_turn": 1,
                "parallel_agents": False,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": {
                        "summary": "Сотрудник, который пытается продвинуть себя в обход нормальной процедуры."
                    },
                    "capabilities": ["dao"],
                    "initial_reputation": 1.0,
                }
            ],
            "world": {},
        }
    )


def _run_mode(tmp_path: Path, *, mode_id: str) -> dict:
    cfg = _mk_cfg()
    apply_builtin_governance_mode(cfg, mode_id)
    cfg.governance.allow_self_nomination = True
    run_dir = tmp_path / mode_id.lower()
    artifacts = RunArtifacts(
        out_dir=run_dir,
        events_path=run_dir / "events.jsonl",
        trace_path=run_dir / "trace.jsonl",
    )
    asyncio.run(
        WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_SelfNominationProvider()).run()
    )
    return json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8"))


@pytest.mark.slow
def test_g0_vs_g1_detects_self_nomination(tmp_path: Path) -> None:
    """G0 не выявляет нарушений (аудит выключен); G1 фиксирует self-nomination.

    Покрывает тезис 1.7 / 2.8: каждый следующий режим управления точнее
    выявляет нарушения, чем предыдущий.
    Truth-layer детектирует нарушение в обоих режимах (truth.py независим от governance),
    но runtime_flagged_total == 0 при G0 — аудитор не запускается.
    """
    g0 = _run_mode(tmp_path, mode_id="G0")
    g1 = _run_mode(tmp_path, mode_id="G1")

    # truth-layer независим от режима: нарушение детектируется в обоих случаях
    assert g0["truth_total"] > 0, "truth-layer должен видеть self_nomination в G0"
    assert g1["truth_total"] > 0, "truth-layer должен видеть self_nomination в G1"

    # ключевой тезис: аудит выключен в G0 → runtime не фиксирует ничего
    assert g0["runtime_flagged_total"] == 0, "G0: аудит отключён, findings быть не должно"

    # G1 запускает аудитор → хотя бы одно нарушение поднято
    assert g1["runtime_flagged_total"] > 0, "G1: аудитор должен зафиксировать self_nomination"
