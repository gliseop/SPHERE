from __future__ import annotations

import json
from pathlib import Path

import asyncio

from sphere_lc.llm import MockLLMProvider

from sphere_lc.config import ScenarioConfig
from sphere_lc.engine import RunArtifacts, WorldEngine


def test_sphere_lc_runs_one_tick(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-smoke",
            "ticks": 1,
            "agents": [
                {"agent_id": "agent:off_1", "name": "Off 1", "internal": True, "persona": "test"},
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = asyncio.run(engine.run())
    assert state.tick == 0
    assert artifacts.events_path.exists()
    assert artifacts.trace_path.exists()

    events = [
        json.loads(line)
        for line in artifacts.events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    snapshots = [e for e in events if e.get("event_type") == "reputation_snapshot"]
    assert snapshots
    assert snapshots[0]["agent_id"] == "agent:off_1"
    assert snapshots[0]["payload"]["target_agent_id"] == "agent:off_1"
    assert snapshots[0]["payload"]["score"] == 0.0


def test_sphere_lc_rejects_phantom_message(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-phantom",
            "ticks": 1,
            "runtime": {"max_actions_per_turn": 1},
            "agents": [
                {"agent_id": "agent:off_1", "name": "Off 1", "internal": True, "persona": "test"},
                {"agent_id": "agent:off_2", "name": "Off 2", "internal": True, "persona": "test"},
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )

    mock = MockLLMProvider(
        structured_responses={
            # По подстроке agent_id в user prompt.
            "Off 1": [
                {
                    "type": "send_message",
                    "to_id": "agent:phantom",
                    "text": "ping",
                    "private": True,
                    "justification": "test",
                }
            ],
            "Off 2": [{"type": "noop", "justification": ""}],
        }
    )

    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=mock)
    asyncio.run(engine.run())

    text = artifacts.events_path.read_text(encoding="utf-8")
    assert "arbiter_rejected" in text
