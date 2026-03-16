from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_lc.agent import AgentRunner
from magistry_lc.config import MemoryConfig, RuntimeConfig, ScenarioConfig
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRegistry
from magistry_lc.events import Event, EventLog
from magistry_lc.fidelity import evaluate_fidelity
from magistry_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from magistry_lc.persona import PersonaArtifact
from magistry_lc.state import AgentState, WorldState
from magistry_lc.tracing import TraceLog
from magistry_lc.worldgen import AgentDailyContext, SceneHook


class _CaptureAgentPromptProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.agent_prompts: list[str] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            self.agent_prompts.append(user)
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "pre"' in user:
            return StructuredLLMResponse(
                data={"events": [], "agent_contexts": [], "scene_hooks": []},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _PreTickWorldgenProvider(_CaptureAgentPromptProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if '"phase": "pre"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [
                        {
                            "audience": "internal",
                            "description": "С утра пришёл внешний сигнал о внеплановой проверке.",
                        }
                    ],
                    "agent_contexts": [
                        {
                            "agent_id": "agent:off_1",
                            "where_day_starts": "У входа в администрацию перед первым совещанием.",
                            "personal_pressure": "Боится, что новый шум ударит по его позиции.",
                            "social_encounter": "Пересёкся с подрядчиком в коридоре.",
                            "ambient_signal": "В новостной ленте обсуждают прозрачность закупок.",
                            "today_hook": "Можно жёстко формализовать процесс или решить вопрос тихо.",
                            "lightweight_contacts": ["старый знакомый", "журналист районной газеты"],
                        }
                    ],
                    "scene_hooks": [
                        {
                            "kind": "corridor_encounter",
                            "agents": ["agent:off_1"],
                            "description": "В коридоре его останавливает подрядчик с короткой просьбой.",
                            "mandatory": False,
                        }
                    ],
                },
                model="mock",
            )
        return super().generate_structured(system, user, schema, temperature)


class _FreeformTruthProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.agent_prompts: list[tuple[int, str]] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "post-hoc recorder нарушений" in system:
            return StructuredLLMResponse(
                data=[
                    {
                        "tick": 0,
                        "subject_agent_id": "agent:off_1",
                        "target_agent_id": "agent:off_2",
                        "violation_type_freeform": "pressure_not_to_escalate",
                        "summary": "Агент давит на коллегу, чтобы та не поднимала вопрос официально.",
                        "mechanism": "private_pressure",
                        "beneficiary": "agent:off_1",
                        "confidence": 0.72,
                        "evidence_refs": [{"tick": 0, "event_type": "message_sent"}],
                        "notes": "freeform",
                    }
                ],
                model="mock",
            )
        if "Сгенерируй действия на этот тик." in user:
            self.agent_prompts.append((0, user))
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _DormantEcologyProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[tuple[int, str]] = []

    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            tick = 0
            marker = "Раунд (tick): "
            if marker in user:
                tick = int(user.split(marker, 1)[1].split("\n", 1)[0])
            if "(agent:spawner)." in user and tick == 0:
                self.prompts.append((tick, "agent:spawner"))
                return StructuredLLMResponse(
                    data={
                        "actions": [
                            {
                                "type": "spawn_agent",
                                "slug": "witness",
                                "name": "Анна Свиридова",
                                "internal": False,
                                "persona_hint": "Внешний наблюдатель.",
                                "capabilities": ["message"],
                            }
                        ]
                    },
                    model="mock",
                )
            if "(agent:witness)." in user:
                self.prompts.append((tick, "agent:witness"))
                return StructuredLLMResponse(
                    data={"actions": [{"type": "noop", "justification": "idle"}]},
                    model="mock",
                )
            if "(agent:observer)." in user:
                self.prompts.append((tick, "agent:observer"))
                return StructuredLLMResponse(
                    data={"actions": [{"type": "noop", "justification": "idle"}]},
                    model="mock",
                )
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


def test_runtime_config_simulated_datetime_respects_granularity() -> None:
    hourly = RuntimeConfig(start_date="2026-03-09", tick_granularity="hour", tick_duration_days=2)
    assert hourly.simulated_datetime(3).isoformat() == "2026-03-09T15:00:00"

    half_day = RuntimeConfig(start_date="2026-03-09", tick_granularity="half_day", tick_duration_days=1)
    assert half_day.simulated_datetime(1).isoformat() == "2026-03-09T21:00:00"

    weekly = RuntimeConfig(start_date="2026-03-09", tick_granularity="week", tick_duration_days=1)
    assert weekly.simulated_datetime(1).date().isoformat() == "2026-03-16"


def test_agent_prompt_includes_story_state_daily_context_and_soft_perform(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        story_state="Последние сдвиги: боится внешнего шума и давления со стороны знакомых.",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[Event(tick=0, event_type="world_event", payload={"description": "Появился риск внешней проверки."})],
        mem_text="(пусто)",
        daily_context=AgentDailyContext(
            where_day_starts="В машине перед работой.",
            personal_pressure="Думает, как избежать публичного скандала.",
            social_encounter="Случайно увидел подрядчика.",
            ambient_signal="В чате обсуждают жалобу.",
            today_hook="Есть соблазн решить вопрос неформально.",
            lightweight_contacts=["бывший начальник"],
        ),
        scene_hooks=[
            SceneHook(
                kind="corridor_encounter",
                agents=["agent:off_1"],
                description="В коридоре ждёт подрядчик.",
            )
        ],
    )

    assert "Твоя ситуация прямо сейчас:" in prompt
    assert "Контекст начала дня:" in prompt
    assert "Личная линия (story state):" not in prompt  # story_state идёт через память/мотивацию, не как отдельный дубль
    assert "Лёгкие контакты не имеют typed-id" in prompt
    assert "если реальный шаг лучше описывается неформально" in prompt
    assert "ПРЕДПОЧИТАЙ структурированные действия" not in prompt


def test_engine_emits_scripted_events_before_agent_turn(tmp_path: Path) -> None:
    provider = _CaptureAgentPromptProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "scripted-event-pre-turn",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник, который избегает публичного шума.",
                    "capabilities": ["message"],
                }
            ],
            "scripted_events": [
                {
                    "event_id": "fork_1",
                    "tick": 0,
                    "audience": "internal",
                    "description": "Министерство внезапно требует ускорить подготовку документов.",
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

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert provider.agent_prompts
    assert "Министерство внезапно требует ускорить подготовку документов." in provider.agent_prompts[0]

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scripted = [event for event in events if event.get("event_type") == "world_event"]
    assert scripted
    assert scripted[0]["payload"]["source"] == "scripted"


def test_engine_pre_tick_worldgen_injects_daily_context(tmp_path: Path) -> None:
    provider = _PreTickWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "pre-tick-worldgen-context",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_pre_tick": True,
                "worldgen_every_ticks": 1,
                "agent_context_budget_per_tick": 2,
                "max_scene_changes_per_tick": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Руководитель, который боится проверок и не любит публичный конфликт.",
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

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert provider.agent_prompts
    prompt = provider.agent_prompts[0]
    assert "Контекст начала дня:" in prompt
    assert "Можно жёстко формализовать процесс или решить вопрос тихо." in prompt
    assert "Сценовые поводы:" in prompt
    assert "С утра пришёл внешний сигнал о внеплановой проверке." in prompt
    assert state.agents["agent:off_1"].story_state.strip()


def test_fidelity_detects_narrating_leakage(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.extend(
        [
            Event(
                tick=0,
                event_type="entity_created",
                actor_id=None,
                payload={
                    "entity_id": "agent:auditor",
                    "kind": "agent",
                    "meta": {"name": "Аудитор"},
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="work_item_created",
                actor_id=None,
                payload={
                    "work_id": "work:A-001",
                    "work_type": "task",
                    "title": "Проверка",
                    "description": "",
                    "participants": [],
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={
                    "description": "Аудитор подписал итоговый отчёт по делу A-001 и дело закрыто."
                },
                audience=["aud:internal"],
            ),
        ]
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.narrating_leakage_total == 1


def test_fidelity_counts_perform_from_approved_payload(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.append(
        Event(
            tick=0,
            event_type="arbiter_approved",
            actor_id="agent:off_1",
            payload={
                "reason": "approved",
                "action": "{'type': <ActionType.PERFORM: 'perform'>, 'description': 'Неформально обсудить сроки', 'target_id': 'agent:off_2'}",
            },
            audience=["aud:internal"],
        )
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.perform_approved_total == 1


def test_fidelity_ignores_media_reports_without_named_actor_leakage(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event_log = EventLog(events_path)
    event_log.extend(
        [
            Event(
                tick=0,
                event_type="work_item_created",
                actor_id=None,
                payload={
                    "work_id": "work:T-001",
                    "work_type": "task",
                    "title": "Тендер",
                    "description": "",
                    "participants": [],
                },
                audience=["aud:internal"],
            ),
            Event(
                tick=0,
                event_type="world_event",
                actor_id=None,
                payload={
                    "description": "Местные СМИ сообщили, что участники тендера T-001 пока не предоставили полную информацию."
                },
                audience=["aud:public"],
            ),
        ]
    )

    summary = evaluate_fidelity(
        events_path=events_path,
        start_date=None,
        tick_duration_days=1,
        temporal_past_slack_days=1,
        temporal_future_horizon_days=30,
    )

    assert summary.narrating_leakage_total == 0


def test_engine_writes_freeform_truth_sidecar_when_enabled(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "freeform-truth",
            "ticks": 1,
            "runtime": {
                "freeform_truth_enabled": True,
                "freeform_truth_window_ticks": 2,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "test",
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

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=_FreeformTruthProvider()).run())

    truth_freeform_path = tmp_path / "truth_freeform.jsonl"
    assert truth_freeform_path.exists()
    lines = [json.loads(line) for line in truth_freeform_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["violation_type_freeform"] == "pressure_not_to_escalate"

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["freeform_truth_total"] == 1


def test_ecology_activation_skips_dormant_spawned_actor(tmp_path: Path) -> None:
    provider = _DormantEcologyProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "ecology-activation",
            "ticks": 3,
            "runtime": {
                "allow_runtime_spawn": True,
                "ecology_activation_window_ticks": 1,
                "max_actions_per_turn": 1,
                "max_agents": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:spawner",
                    "name": "Spawner",
                    "internal": True,
                    "persona": "test",
                    "capabilities": ["message", "spawn"],
                },
                {
                    "agent_id": "agent:observer",
                    "name": "Observer",
                    "internal": True,
                    "persona": "test",
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

    asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    witness_ticks = [tick for tick, actor in provider.prompts if actor == "agent:witness"]
    assert witness_ticks == [1]
