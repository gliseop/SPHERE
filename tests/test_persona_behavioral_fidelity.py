from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sphere_lc.agent import AgentRunner
from sphere_lc.config import MemoryConfig, RuntimeConfig
from sphere_lc.entities import EntityRegistry
from sphere_lc.events import Event
from sphere_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from sphere_lc.persona import MotivationDigest, PersonaArtifact
from sphere_lc.state import AgentState, WorldState
from sphere_lc.tracing import TraceLog


class _PersonaSensitiveProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ) -> StructuredLLMResponse:
        lowered = user.casefold()
        if "держаться формальных процедур" in lowered:
            return StructuredLLMResponse(
                data={"proposal": "Подготовлю официальную записку и не пойду на неформальный риск."},
                model="mock",
            )
        if "личная выгода важнее формальной процедуры" in lowered:
            return StructuredLLMResponse(
                data={"proposal": "Сначала тихо договорюсь с нужным человеком, а след оформлю потом."},
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


def test_persona_motivation_changes_agent_prompt_and_proposal(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    provider = _PersonaSensitiveProvider()
    runner = AgentRunner(
        llm=LLMCaller(provider=provider, trace=TraceLog(trace_path)),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    honest_agent = AgentState(
        agent_id="agent:honest",
        name="Честный сотрудник",
        internal=True,
        capabilities=["message", "work"],
        persona=PersonaArtifact(
            summary="Осторожный и честный сотрудник.",
            motivation=MotivationDigest(
                goal="Сохранить процесс прозрачным и не создавать лишний риск.",
                gain="Выгодно держаться формальных процедур и не ставить себя под удар.",
            ),
        ),
    )
    opportunist_agent = AgentState(
        agent_id="agent:opportunist",
        name="Оппортунистичный сотрудник",
        internal=True,
        capabilities=["message", "work"],
        persona=PersonaArtifact(
            summary="Сотрудник, склонный к личной выгоде.",
            motivation=MotivationDigest(
                goal="Получить личное преимущество из ситуации.",
                gain="Личная выгода важнее формальной процедуры, если риск выглядит управляемым.",
            ),
        ),
    )
    state = WorldState(
        tick=0,
        registry=EntityRegistry(),
        agents={
            honest_agent.agent_id: honest_agent,
            opportunist_agent.agent_id: opportunist_agent,
        },
    )
    visible_events = [
        Event(
            tick=0,
            event_type="world_event",
            actor_id=None,
            payload={"description": "Нужно срочно решить спорный закупочный вопрос."},
            audience=["aud:internal"],
        )
    ]

    honest_actions = asyncio.run(
        runner.propose_actions(agent=honest_agent, state=state, visible_events=visible_events)
    )
    opportunist_actions = asyncio.run(
        runner.propose_actions(agent=opportunist_agent, state=state, visible_events=visible_events)
    )

    assert honest_actions[0].description != opportunist_actions[0].description
    assert "официальную записку" in honest_actions[0].description
    assert "тихо договорюсь" in opportunist_actions[0].description

    trace_rows = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    agent_prompts = [row["user"] for row in trace_rows if row.get("role") == "agent"]

    assert len(agent_prompts) == 2
    assert agent_prompts[0] != agent_prompts[1]
    assert any("держаться формальных процедур" in prompt for prompt in agent_prompts)
    assert any("Личная выгода важнее формальной процедуры" in prompt for prompt in agent_prompts)
