from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_lc.events import Event
from magistry_lc.agent import AgentRunner
from magistry_lc.config import MemoryConfig, RuntimeConfig, ScenarioConfig
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRegistry
from magistry_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from magistry_lc.persona import PersonaArtifact
from magistry_lc.state import AgentState, WorldState
from magistry_lc.tracing import TraceLog
from magistry_lc.worldgen import WorldGenerator


def _mk_cfg(*, enrich_personas: bool, persona_summary: str = "Краткая персона") -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "lc-persona-enrich",
            "ticks": 1,
            "runtime": {
                "max_actions_per_turn": 1,
                "enrich_personas": enrich_personas,
                "persona_enrich_mode": "core",
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": persona_summary,
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )


def test_engine_enriches_personas_and_writes_cache(tmp_path: Path) -> None:
    cfg = _mk_cfg(enrich_personas=True)
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider()).run())

    persona = state.agents["agent:off_1"].persona
    assert persona.summary.strip()
    assert persona.biography.strip()

    cache_path = tmp_path / "personas.json"
    assert cache_path.exists()
    cache_raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache_raw["meta"]["fingerprint"]
    assert cache_raw["personas"]["agent:off_1"]["biography"].strip()


class _FailOnPersonaCallsProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Подсказка/черновик персоны" in user:
            raise AssertionError("persona generation should be skipped when cache is valid")
        return super().generate_structured(system, user, schema, temperature)


def test_engine_uses_persona_cache_without_persona_llm_calls(tmp_path: Path) -> None:
    cfg = _mk_cfg(enrich_personas=True)
    first_artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events_first.jsonl",
        trace_path=tmp_path / "trace_first.jsonl",
    )
    asyncio.run(WorldEngine(cfg=cfg, artifacts=first_artifacts, provider_override=MockLLMProvider()).run())

    second_artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events_second.jsonl",
        trace_path=tmp_path / "trace_second.jsonl",
    )
    state = asyncio.run(
        WorldEngine(
            cfg=cfg,
            artifacts=second_artifacts,
            provider_override=_FailOnPersonaCallsProvider(),
        ).run()
    )
    assert state.agents["agent:off_1"].persona.biography.strip()

    spans = [
        json.loads(line)
        for line in second_artifacts.trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not any(span.get("role") == "persona" for span in spans)


def test_engine_without_enrich_keeps_empty_biography(tmp_path: Path) -> None:
    cfg = _mk_cfg(enrich_personas=False)
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider()).run())
    assert state.agents["agent:off_1"].persona.biography == ""


def test_render_memory_keeps_full_summary_and_adds_biography_excerpt(tmp_path: Path) -> None:
    summary = "S" * 500 + "_tail"
    biography = "B" * 700
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary=summary, biography=biography),
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    mem_text = asyncio.run(runner._render_memory(agent=agent, state=state, visible_events=[]))
    assert summary[-20:] in mem_text
    assert "Биография (начало):" in mem_text
    assert "…" in mem_text


class _SocialGraphProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Выдели до" in user and "agent:head" in user:
            return StructuredLLMResponse(
                data={
                    "links": [
                        {
                            "name": "Волкова Наталья Ивановна",
                            "relation": "жена",
                            "relevance": "Знает бытовой фон и влияет на решения Волкова.",
                            "persona_hint": "Учитель математики, замечает перемены в поведении мужа.",
                            "internal": False,
                            "capabilities": ["message"],
                        }
                    ]
                },
                model="mock",
            )
        if "Выдели до" in user:
            return StructuredLLMResponse(data={"links": []}, model="mock")
        return super().generate_structured(system, user, schema, temperature)


class _RuntimeSpawnProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.spawned_agent_acted = False

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            if "agent:spawner" in user and "Раунд (tick): 0" in user:
                return StructuredLLMResponse(
                    data={
                        "actions": [
                            {
                                "type": "spawn_agent",
                                "slug": "witness",
                                "name": "Свидетель",
                                "internal": False,
                                "persona_hint": "Внешний наблюдатель, который знает детали сделки.",
                                "capabilities": ["message"],
                            }
                        ]
                    },
                    model="mock",
                )
            if "agent:witness" in user:
                self.spawned_agent_acted = True
                return StructuredLLMResponse(data={"actions": []}, model="mock")
            return StructuredLLMResponse(data={"actions": []}, model="mock")
        return super().generate_structured(system, user, schema, temperature)


class _DoubleRuntimeSpawnProvider(_RuntimeSpawnProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user and "agent:spawner" in user and "Раунд (tick): 0" in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "spawn_agent",
                            "slug": "witness_a",
                            "name": "Свидетель А",
                            "internal": False,
                            "persona_hint": "Первый свидетель.",
                            "capabilities": ["message"],
                        },
                        {
                            "type": "spawn_agent",
                            "slug": "witness_b",
                            "name": "Свидетель Б",
                            "internal": False,
                            "persona_hint": "Второй свидетель.",
                            "capabilities": ["message"],
                        },
                    ]
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _WorldgenSpawnProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.spawned_agent_acted = False

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            if "agent:journalist" in user:
                self.spawned_agent_acted = True
            return StructuredLLMResponse(data={"actions": []}, model="mock")
        if "\"tick\": 0" in user:
            return StructuredLLMResponse(
                data={
                    "events": [
                        {"audience": "public", "description": "В районной газете заметили странный тендер."}
                    ],
                    "spawns": [
                        {
                            "slug": "journalist",
                            "name": "Журналист",
                            "internal": False,
                            "persona_hint": "Настырный корреспондент районной газеты.",
                            "reason": "Публикации в канале администрации вызвали интерес редакции.",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _LegacyWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        return StructuredLLMResponse(
            data=[{"audience": "internal", "description": "Legacy worldgen event"}],
            model="mock",
        )


def test_engine_spawns_secondary_agents_before_first_tick(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "secondary-spawn",
            "ticks": 1,
            "runtime": {
                "max_actions_per_turn": 1,
                "enrich_personas": True,
                "persona_enrich_mode": "core",
                "spawn_secondary": True,
                "max_secondary_per_agent": 1,
                "max_agents": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Волков",
                    "internal": True,
                    "persona": {
                        "summary": "Руководитель закупок.",
                        "biography": "Женат на Наталье Ивановне, учительнице математики. Обсуждает с ней рабочие тревоги.",
                        "interview": [],
                    },
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(
        WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_SocialGraphProvider()).run()
    )

    secondary_ids = [aid for aid in state.agents if aid.startswith("agent:sec_")]
    assert len(secondary_ids) == 1
    secondary = state.agents[secondary_ids[0]]
    assert secondary.persona.biography.strip()
    assert any(secondary.agent_id in doc.text for doc in state.agents["agent:head"].memory.docs)
    assert any("Связь:" in doc.text for doc in secondary.memory.docs)


def test_engine_limits_secondary_agents_by_max_agents(tmp_path: Path) -> None:
    class _TwoLinksProvider(_SocialGraphProvider):
        def generate_structured(self, system: str, user: str, schema: dict, temperature: float = 0.0):
            if "Выдели до" in user and "agent:head" in user:
                return StructuredLLMResponse(
                    data={
                        "links": [
                            {
                                "name": "Волкова Наталья Ивановна",
                                "relation": "жена",
                                "relevance": "Влияет на бытовые решения.",
                                "persona_hint": "Учитель математики.",
                                "internal": False,
                                "capabilities": ["message"],
                            },
                            {
                                "name": "Семенов П.А.",
                                "relation": "друг",
                                "relevance": "Давний знакомый и советчик.",
                                "persona_hint": "Юрист, иногда помогает советом.",
                                "internal": False,
                                "capabilities": ["message"],
                            },
                        ]
                    },
                    model="mock",
                )
            return super().generate_structured(system, user, schema, temperature)

    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "secondary-limit",
            "ticks": 1,
            "runtime": {
                "max_actions_per_turn": 1,
                "spawn_secondary": True,
                "max_secondary_per_agent": 2,
                "max_agents": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Волков",
                    "internal": True,
                    "persona": {
                        "summary": "Руководитель закупок.",
                        "biography": "Женат на Наталье Ивановне и советуется с Семеновым.",
                        "interview": [],
                    },
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(
        WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_TwoLinksProvider()).run()
    )
    assert len(state.agents) == 2


def test_runtime_spawn_registers_new_agent_for_next_tick(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "runtime-spawn",
            "ticks": 2,
            "runtime": {
                "max_actions_per_turn": 1,
                "allow_runtime_spawn": True,
                "max_agents": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:spawner",
                    "name": "Spawner",
                    "internal": True,
                    "persona": "Старший участник процесса.",
                    "capabilities": ["message", "spawn"],
                },
                {
                    "agent_id": "agent:observer",
                    "name": "Observer",
                    "internal": True,
                    "persona": "Наблюдатель.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    provider = _RuntimeSpawnProvider()
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "agent:witness" in state.agents
    assert provider.spawned_agent_acted is True
    assert any(doc.kind == "persona" for doc in state.agents["agent:witness"].memory.docs)


def test_runtime_spawn_respects_max_agents_limit(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "runtime-spawn-limit",
            "ticks": 1,
            "runtime": {
                "max_actions_per_turn": 1,
                "allow_runtime_spawn": True,
                "max_agents": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:spawner",
                    "name": "Spawner",
                    "internal": True,
                    "persona": "Старший участник процесса.",
                    "capabilities": ["message", "spawn"],
                },
                {
                    "agent_id": "agent:observer",
                    "name": "Observer",
                    "internal": True,
                    "persona": "Наблюдатель.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(
        WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_RuntimeSpawnProvider()).run()
    )
    assert "agent:witness" not in state.agents


def test_runtime_spawn_allows_only_one_spawn_per_agent_per_tick(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "runtime-spawn-single-per-tick",
            "ticks": 1,
            "runtime": {
                "max_actions_per_turn": 2,
                "allow_runtime_spawn": True,
                "max_agents": 5,
            },
            "agents": [
                {
                    "agent_id": "agent:spawner",
                    "name": "Spawner",
                    "internal": True,
                    "persona": "Старший участник процесса.",
                    "capabilities": ["message", "spawn"],
                },
                {
                    "agent_id": "agent:observer",
                    "name": "Observer",
                    "internal": True,
                    "persona": "Наблюдатель.",
                    "capabilities": ["message"],
                },
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(
        WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_DoubleRuntimeSpawnProvider()).run()
    )
    spawned = [aid for aid in state.agents if aid.startswith("agent:witness_")]
    assert len(spawned) == 1


def test_worldgen_spawn_registers_new_agent(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "worldgen-spawn",
            "ticks": 2,
            "runtime": {
                "max_actions_per_turn": 1,
                "allow_runtime_spawn": True,
                "max_agents": 3,
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:seed",
                    "name": "Seed",
                    "internal": True,
                    "persona": "Базовый агент.",
                    "capabilities": ["message"],
                }
            ],
            "world": {"channels": [{"channel_id": "chan:public", "title": "public"}]},
        }
    )
    provider = _WorldgenSpawnProvider()
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "agent:journalist" in state.agents
    assert provider.spawned_agent_acted is True


def test_worldgen_accepts_legacy_list_response(tmp_path: Path) -> None:
    trace = TraceLog(tmp_path / "trace.jsonl")
    llm = LLMCaller(provider=_LegacyWorldgenProvider(), trace=trace)
    wg = WorldGenerator(llm=llm, temperature=0.0)
    out = asyncio.run(
        wg.generate(
            tick=0,
            recent_events=[
                Event(
                    tick=0,
                    event_type="world_event",
                    actor_id=None,
                    payload={"description": "seed"},
                    audience=["aud:public"],
                )
            ],
            language="ru",
        )
    )
    assert len(out.events) == 1
    assert out.events[0].event_type == "world_event"
    assert out.spawns == []
