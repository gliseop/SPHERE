from __future__ import annotations

import asyncio
import json
from pathlib import Path

from magistry_lc.agent import AgentRunner
from magistry_lc.arbiter import Arbiter
from magistry_lc.config import MemoryConfig, RuntimeConfig, ScenarioConfig
from magistry_lc.dao import DaoEngine
from magistry_lc.engine import RunArtifacts, WorldEngine
from magistry_lc.entities import EntityRecord, EntityRegistry
from magistry_lc.events import Event, EventLog
from magistry_lc.fidelity import evaluate_fidelity
from magistry_lc.id_alloc import IdAllocator
from magistry_lc.ids import EntityKind
from magistry_lc.ids import INTERNAL_AUDIENCE
from magistry_lc.journal import WorldJournal
from magistry_lc.llm import LLMCaller, MockLLMProvider, StructuredLLMResponse
from magistry_lc.persona import PersonaArtifact
from magistry_lc.state import (
    AgentState,
    ArtifactState,
    InstitutionRegimeState,
    ResourcePoolState,
    WorkItem,
    WorldState,
    ZoneState,
)
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


class _EnvironmentUpdateProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "environment_updates": {
                        "institutions": [
                            {
                                "org_id": "org:city_hall",
                                "operating_mode": "crisis",
                                "security_mode": "heightened",
                            }
                        ],
                        "zones": [
                            {
                                "zone_id": "zone:city_hall",
                                "access_mode": "restricted",
                            }
                        ],
                        "resource_pools": [
                            {
                                "resource_id": "res:roads_budget",
                                "quantity": 900,
                                "status": "depleted",
                                "pressure": "Подрядчики требуют срочного решения.",
                            }
                        ],
                        "information_climate": {
                            "public_mood": "Раздражение усиливается.",
                            "media_pressure": "Журналисты готовят материал.",
                            "active_signals": ["новая волна жалоб", "утечка сметы"],
                        },
                    },
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _ArtifactWorldgenProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [],
                    "artifact_creations": [
                        {
                            "artifact_id": "art:oversight_memo",
                            "artifact_type": "memo",
                            "title": "Служебная записка контрольного отдела",
                            "summary": "Контрольный отдел просит срочно пояснить расходование средств.",
                            "owner_org_id": "org:city_hall",
                            "visibility": "internal",
                            "status": "new",
                            "tags": ["oversight", "budget"],
                        }
                    ],
                    "artifact_updates": [
                        {
                            "artifact_id": "art:repair_report",
                            "summary": "В отчёте появился новый спорный абзац о перерасходе.",
                            "status": "revised",
                            "tags": ["repair", "risk"],
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _BoundSpawnProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "Сгенерируй действия на этот тик." in user:
            return StructuredLLMResponse(
                data={"actions": [{"type": "noop", "justification": "idle"}]},
                model="mock",
            )
        if '"phase": "post"' in user:
            return StructuredLLMResponse(
                data={
                    "events": [],
                    "spawns": [
                        {
                            "slug": "district_reporter",
                            "name": "Ирина Савельева",
                            "internal": False,
                            "persona_hint": "Местная журналистка, следит за работой мэрии.",
                            "org_id": "org:city_hall",
                            "zone_id": "zone:city_hall",
                        }
                    ],
                },
                model="mock",
            )
        return StructuredLLMResponse(data={"events": [], "spawns": []}, model="mock")


class _MicroReactionProvider(MockLLMProvider):
    def generate_structured(
        self,
        system: str,
        user: str,
        schema: dict,
        temperature: float = 0.0,
    ):
        if "agent:core_1" in user and "локальное окно реакции" not in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:peripheral",
                            "text": "Срочно посмотри на это.",
                            "private": True,
                            "justification": "Нужно быстpoе подтверждение.",
                        }
                    ]
                },
                model="mock",
            )
        if "agent:peripheral" in user and "локальное окно реакции" in user:
            return StructuredLLMResponse(
                data={
                    "actions": [
                        {
                            "type": "send_message",
                            "to_id": "agent:core_1",
                            "text": "Принял, уже смотрю.",
                            "private": True,
                            "justification": "Реакция на сообщение того же тика.",
                        }
                    ]
                },
                model="mock",
            )
        return StructuredLLMResponse(
            data={"actions": [{"type": "noop", "justification": "idle"}]},
            model="mock",
        )


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


def test_agent_prompt_includes_relevant_environment_brief(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.environment.institutions["org:city_hall"] = InstitutionRegimeState(
        org_id="org:city_hall",
        operating_mode="strained",
        transparency_mode="limited",
        access_mode="restricted",
        security_mode="heightened",
        capture_risk="medium",
    )
    state.environment.zones["zone:city_hall"] = ZoneState(
        zone_id="zone:city_hall",
        title="Здание мэрии",
        primary_org_id="org:city_hall",
        access_mode="controlled",
        transparency_mode="internal",
        security_level="heightened",
    )
    state.environment.resource_pools["res:roads_budget"] = ResourcePoolState(
        resource_id="res:roads_budget",
        title="Бюджет дорожного ремонта",
        owner_org_id="org:city_hall",
        quantity=900,
        unit="тыс. руб.",
        status="depleted",
        pressure="Подрядчики требуют срочного решения.",
    )
    state.environment.information_climate.public_mood = "Раздражение усиливается."
    state.environment.information_climate.media_pressure = "Журналисты готовят материал."
    state.environment.information_climate.active_signals = ["новая волна жалоб", "утечка сметы"]
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Релевантная среда:" in prompt
    assert "Организация org:city_hall" in prompt
    assert "Зона zone:city_hall" in prompt
    assert "Ресурс res:roads_budget" in prompt
    assert "Активные сигналы среды: новая волна жалоб, утечка сметы" in prompt


def test_agent_prompt_includes_relevant_artifacts(tmp_path: Path) -> None:
    agent = AgentState(
        agent_id="agent:off_1",
        name="Off 1",
        internal=True,
        persona=PersonaArtifact(summary="Хочет удержать процесс под контролем."),
        capabilities=["message", "work"],
        org_id="org:city_hall",
        zone_id="zone:city_hall",
        title="начальник отдела",
    )
    state = WorldState(tick=0, registry=EntityRegistry(), agents={agent.agent_id: agent})
    state.work_items["work:repair"] = WorkItem(
        work_id="work:repair",
        work_type="repair",
        title="Ремонт дороги",
        participants=[agent.agent_id],
    )
    state.artifacts["art:oversight_memo"] = ArtifactState(
        artifact_id="art:oversight_memo",
        artifact_type="memo",
        title="Служебная записка",
        summary="Контрольный отдел просит пояснения.",
        owner_org_id="org:city_hall",
        visibility="internal",
        status="new",
        tags=["oversight"],
    )
    state.artifacts["art:repair_report"] = ArtifactState(
        artifact_id="art:repair_report",
        artifact_type="report",
        title="Отчёт по ремонту",
        summary="В отчёте отмечен спорный перерасход.",
        zone_id="zone:city_hall",
        related_work_id="work:repair",
        visibility="internal",
        status="revised",
        tags=["repair", "risk"],
    )
    runner = AgentRunner(
        llm=LLMCaller(provider=MockLLMProvider(), trace=TraceLog(tmp_path / "trace.jsonl")),
        runtime=RuntimeConfig(),
        memory=MemoryConfig(),
    )

    prompt = runner._build_user(
        agent=agent,
        state=state,
        visible_events=[],
        mem_text="(пусто)",
    )

    assert "Релевантные документы и артефакты:" in prompt
    assert "art:oversight_memo" in prompt
    assert "art:repair_report" in prompt
    assert "work=work:repair" in prompt


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


def test_environment_layer_is_initialized_and_exposed_to_worldgen_snapshot(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-layer",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [
                        {
                            "org_id": "org:city_hall",
                            "operating_mode": "strained",
                            "transparency_mode": "limited",
                            "access_mode": "restricted",
                            "security_mode": "heightened",
                            "capture_risk": "medium",
                            "linked_zone_ids": ["zone:city_hall"],
                        }
                    ],
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "zone_type": "office",
                            "primary_org_id": "org:city_hall",
                            "access_mode": "controlled",
                            "transparency_mode": "internal",
                            "security_level": "heightened",
                        }
                    ],
                    "resource_pools": [
                        {
                            "resource_id": "res:roads_budget",
                            "title": "Бюджет дорожного ремонта",
                            "owner_org_id": "org:city_hall",
                            "quantity": 1250,
                            "unit": "тыс. руб.",
                            "status": "strained",
                            "pressure": "Сроки поджимают, подрядчики нервничают.",
                        }
                    ],
                    "information_climate": {
                        "public_mood": "Недоверие к обещаниям администрации.",
                        "oversight_attention": "Высокое внимание контрольного управления.",
                        "media_pressure": "Локальные медиа ищут повод для сюжета.",
                        "narrative_temperature": "Напряжённая повестка перед сессией совета.",
                        "active_signals": ["жалобы на задержки ремонта", "слухи о фаворитизме"],
                    },
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = engine._init_state(event_log=EventLog(artifacts.events_path))

    assert state.registry.list_ids(EntityKind.ZONE) == ["zone:city_hall"]
    assert state.registry.list_ids(EntityKind.RESOURCE) == ["res:roads_budget"]
    assert state.environment.institutions["org:city_hall"].security_mode == "heightened"

    snapshot = engine._build_worldgen_state_snapshot(state=state)
    assert snapshot["environment"]["counts"] == {
        "institutions": 1,
        "zones": 1,
        "resource_pools": 1,
    }
    assert snapshot["environment"]["resource_pools"][0]["resource_id"] == "res:roads_budget"
    assert snapshot["environment"]["information_climate"]["public_mood"] == "Недоверие к обещаниям администрации."

    journal = WorldJournal.from_state(state=state)
    journal_dict = journal.to_dict()
    assert journal_dict["entities"]["zones"] == 1
    assert journal_dict["entities"]["resource_pools"] == 1
    assert journal_dict["environment"]["institutions"][0]["org_id"] == "org:city_hall"


def test_post_worldgen_can_update_environment_layer(tmp_path: Path) -> None:
    provider = _EnvironmentUpdateProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-updates",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [
                        {
                            "org_id": "org:city_hall",
                            "operating_mode": "strained",
                            "security_mode": "routine",
                        }
                    ],
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "primary_org_id": "org:city_hall",
                        }
                    ],
                    "resource_pools": [
                        {
                            "resource_id": "res:roads_budget",
                            "title": "Бюджет дорожного ремонта",
                            "owner_org_id": "org:city_hall",
                            "quantity": 1250,
                            "unit": "тыс. руб.",
                            "status": "stable",
                        }
                    ],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert state.environment.institutions["org:city_hall"].operating_mode == "crisis"
    assert state.environment.institutions["org:city_hall"].security_mode == "heightened"
    assert state.environment.zones["zone:city_hall"].access_mode == "restricted"
    assert state.environment.resource_pools["res:roads_budget"].quantity == 900
    assert state.environment.resource_pools["res:roads_budget"].status == "depleted"
    assert state.environment.information_climate.media_pressure == "Журналисты готовят материал."
    assert state.environment.information_climate.active_signals == ["новая волна жалоб", "утечка сметы"]

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = {event["event_type"] for event in events}
    assert "environment_institution_updated" in event_types
    assert "environment_zone_updated" in event_types
    assert "environment_resource_updated" in event_types
    assert "environment_information_climate_updated" in event_types


def test_post_worldgen_can_create_and_update_artifacts(tmp_path: Path) -> None:
    provider = _ArtifactWorldgenProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "artifact-updates",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                    "org_id": "org:city_hall",
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "artifacts": [
                    {
                        "artifact_id": "art:repair_report",
                        "artifact_type": "report",
                        "title": "Отчёт по ремонту",
                        "summary": "Базовый отчёт.",
                        "owner_org_id": "org:city_hall",
                        "visibility": "internal",
                        "status": "active",
                    }
                ],
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    assert "art:oversight_memo" in state.artifacts
    assert state.artifacts["art:oversight_memo"].artifact_type == "memo"
    assert state.artifacts["art:repair_report"].status == "revised"
    assert "risk" in state.artifacts["art:repair_report"].tags

    events = [json.loads(line) for line in artifacts.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = {event["event_type"] for event in events}
    assert "artifact_created" in event_types
    assert "artifact_updated" in event_types


def test_environment_updates_activate_peripheral_agent(tmp_path: Path) -> None:
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "environment-activation",
            "ticks": 1,
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Ключевой агент",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "institution_modes": [{"org_id": "org:city_hall"}],
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=MockLLMProvider())
    state = engine._init_state(event_log=EventLog(artifacts.events_path))
    state.agents["agent:peripheral"] = AgentState(
        agent_id="agent:peripheral",
        name="Peripheral",
        internal=True,
        persona=PersonaArtifact(summary="Периферийный сотрудник"),
        capabilities=["message"],
        org_id="org:city_hall",
    )
    event = Event(
        tick=0,
        event_type="environment_institution_updated",
        actor_id=None,
        payload={"org_id": "org:city_hall", "operating_mode": "crisis"},
        audience=[INTERNAL_AUDIENCE],
    )

    assert engine._should_activate_agent(
        state=state,
        agent_id="agent:peripheral",
        events_history=[event],
        daily_contexts=None,
        scene_hooks_by_agent=None,
    )


def test_micro_reaction_window_allows_same_tick_reply(tmp_path: Path) -> None:
    provider = _MicroReactionProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "micro-reaction",
            "ticks": 1,
            "runtime": {
                "micro_reaction_rounds": 1,
                "micro_reaction_max_agents_per_round": 3,
            },
            "agents": [
                {
                    "agent_id": "agent:core_1",
                    "name": "Core 1",
                    "internal": True,
                    "persona": "Ключевой агент",
                    "capabilities": ["message"],
                }
            ],
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )
    engine = WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider)
    event_log = EventLog(artifacts.events_path)
    state = engine._init_state(event_log=event_log)
    state.registry.register(
        EntityRecord(
            entity_id="agent:peripheral",
            kind=EntityKind.AGENT,
            created_by=None,
            created_tick=0,
            meta={"name": "Peripheral", "internal": True, "capabilities": ["message"]},
        )
    )
    state.agents["agent:peripheral"] = AgentState(
        agent_id="agent:peripheral",
        name="Peripheral",
        internal=True,
        persona=PersonaArtifact(summary="Периферийный агент"),
        capabilities=["message"],
    )

    trace = TraceLog(artifacts.trace_path)
    llm = LLMCaller(provider=provider, trace=trace)
    runners: dict[str, AgentRunner] = {}
    asyncio.run(
        engine._register_agent_runner(
            agent_id="agent:core_1",
            state=state,
            runners=runners,
            llm=llm,
            embedder=None,
            embed_cache={},
        )
    )
    asyncio.run(
        engine._register_agent_runner(
            agent_id="agent:peripheral",
            state=state,
            runners=runners,
            llm=llm,
            embedder=None,
            embed_cache={},
        )
    )

    proposed, gather_errors = asyncio.run(
        engine._gather_actions(
            state=state,
            runners=runners,
            events_history=[],
            agent_order=["agent:core_1", "agent:peripheral"],
        )
    )
    assert not gather_errors
    assert "agent:peripheral" not in [aid for aid, acts in proposed.items() if acts]

    arbiter = Arbiter(
        llm=llm,
        governance=cfg.governance,
        id_alloc=IdAllocator(),
        dao=DaoEngine(cfg=cfg.governance),
        runtime=cfg.runtime,
        temperature=cfg.llm.temperature,
    )
    tick_events = asyncio.run(
        engine._apply_actions(
            state=state,
            arbiter=arbiter,
            proposed=proposed,
            event_log=event_log,
            agent_order=["agent:core_1"],
            journal_yaml=state.journal_yaml(),
        )
    )
    assert any(ev.event_type == "message_sent" and ev.actor_id == "agent:core_1" for ev in tick_events)

    reaction_events = asyncio.run(
        engine._run_micro_reaction_rounds(
            state=state,
            runners=runners,
            arbiter=arbiter,
            event_log=event_log,
            events_history=[],
            tick_events=tick_events,
            already_acted={"agent:core_1"},
        )
    )

    assert any(
        ev.event_type == "message_sent"
        and ev.actor_id == "agent:peripheral"
        and (ev.payload or {}).get("to_id") == "agent:core_1"
        for ev in reaction_events
    )


def test_worldgen_spawn_can_bind_agent_to_org_and_zone(tmp_path: Path) -> None:
    provider = _BoundSpawnProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "bound-spawn",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_every_ticks": 1,
                "allow_runtime_spawn": True,
            },
            "agents": [
                {
                    "agent_id": "agent:off_1",
                    "name": "Off 1",
                    "internal": True,
                    "persona": "Чиновник",
                    "capabilities": ["message"],
                }
            ],
            "world": {
                "orgs": [{"org_id": "org:city_hall", "title": "Мэрия"}],
                "environment": {
                    "zones": [
                        {
                            "zone_id": "zone:city_hall",
                            "title": "Здание мэрии",
                            "primary_org_id": "org:city_hall",
                        }
                    ]
                },
            },
        }
    )
    artifacts = RunArtifacts(
        out_dir=tmp_path,
        events_path=tmp_path / "events.jsonl",
        trace_path=tmp_path / "trace.jsonl",
    )

    state = asyncio.run(WorldEngine(cfg=cfg, artifacts=artifacts, provider_override=provider).run())

    spawned = state.agents["agent:district_reporter"]
    assert spawned.org_id == "org:city_hall"
    assert spawned.zone_id == "zone:city_hall"


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


def test_engine_injects_fallback_risky_contexts_for_scripted_tick(tmp_path: Path) -> None:
    provider = _CaptureAgentPromptProvider()
    cfg = ScenarioConfig.model_validate(
        {
            "version": 1,
            "title": "fallback-risk-context",
            "ticks": 1,
            "runtime": {
                "enable_worldgen": True,
                "worldgen_pre_tick": True,
                "worldgen_every_ticks": 1,
            },
            "agents": [
                {
                    "agent_id": "agent:head",
                    "name": "Head",
                    "internal": True,
                    "persona": "Руководитель, который старается удержать контроль над процессом.",
                    "capabilities": ["message"],
                },
                {
                    "agent_id": "agent:spec",
                    "name": "Spec",
                    "internal": True,
                    "persona": "Специалист, который боится ошибки и конфликта.",
                    "capabilities": ["message"],
                },
            ],
            "scripted_events": [
                {
                    "tick": 0,
                    "audience": "internal",
                    "description": "Обнаружено подозрительное совпадение формулировок и риск санкций за конфликт интересов.",
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
    prompt = provider.agent_prompts[0]
    assert "Контекст начала дня:" in prompt
    assert "Частное давление:" in prompt
    assert "Возможность/выгода:" in prompt
    assert "Риск раскрытия:" in prompt
    assert "Простой административный ответ может не снять напряжение" in prompt


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
