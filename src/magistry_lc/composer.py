"""WorldComposer: генерация сценария из текстового описания."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import AgentConfig, ChannelConfig, OrgConfig, RuntimeConfig, ScenarioConfig, WorkItemConfig
from .ids import EntityKind, ensure_kind, make_id
from .llm import LLMCaller
from .persona import PersonaArtifact, PersonaGenerator


class _ComposeAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    internal: bool
    persona: str = ""
    initial_title: str = "специалист"
    wants_promotion: bool = True
    capabilities: list[str] = Field(default_factory=list)


class _ComposeWorld(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[dict[str, Any]] = Field(default_factory=list)
    orgs: list[dict[str, Any]] = Field(default_factory=list)
    work_items: list[dict[str, Any]] = Field(default_factory=list)


class _ComposeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    agents: list[_ComposeAgent]
    world: _ComposeWorld = Field(default_factory=_ComposeWorld)


def _compose_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "agents": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "agent_id": {"type": "string"},
                        "name": {"type": "string"},
                        "internal": {"type": "boolean"},
                        "persona": {"type": "string"},
                        "initial_title": {"type": "string"},
                        "wants_promotion": {"type": "boolean"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["agent_id", "name", "internal"],
                },
            },
            "world": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "channels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"channel_id": {"type": "string"}, "title": {"type": "string"}},
                            "required": ["channel_id"],
                        },
                    },
                    "orgs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"org_id": {"type": "string"}, "title": {"type": "string"}},
                            "required": ["org_id"],
                        },
                    },
                    "work_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "work_id": {"type": "string"},
                                "work_type": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "participants": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["work_id", "work_type", "title"],
                        },
                    },
                },
                "required": [],
            },
        },
        "required": ["title", "agents"],
    }


def _normalize_typed_id(raw_id: str, kind: EntityKind) -> str:
    raw_id = (raw_id or "").strip()
    if not raw_id:
        raise ValueError("id must be non-empty")
    if ":" not in raw_id:
        return make_id(kind, raw_id)
    ensure_kind(raw_id, kind)
    return raw_id


@dataclass(slots=True)
class WorldComposer:
    """Сгенерировать ScenarioConfig из текстового описания."""

    llm: LLMCaller
    temperature: float = 0.0
    generate_personas: bool = True

    async def compose(
        self,
        *,
        description: str,
        ticks: int,
        seed: int,
        language: str,
    ) -> ScenarioConfig:
        """Сгенерировать сценарий.

        Args:
            description: Текстовая постановка ситуации.
            ticks: Длина прогона.
            seed: Зерно.
            language: Язык симуляции.
        """
        system = (
            "Ты — генератор сценариев для симуляции организационных процессов (MAGISTRY-LC).\n"
            "Сгенерируй состав мира и агентов из описания.\n"
            "Жёсткие требования:\n"
            "- Используй только типизированные ID: agent:*, org:*, chan:*, work:*.\n"
            "- Вторичные агенты должны появляться по ситуации (не фиксированным числом).\n"
            "- Не используй числовые параметры личности (greed/fear/honesty/etc). Только текст.\n"
            "- Должности и репутация применимы только к internal=true.\n"
            f"- Пиши на языке: {language!r}.\n"
            "Ответ: строго JSON по схеме.\n"
        )
        user = (
            f"Описание:\n{description}\n\n"
            f"Параметры:\n- ticks: {ticks}\n- seed: {seed}\n"
        )
        resp = await self.llm.generate_structured(
            role="composer",
            name="world_composer",
            tick=0,
            system=system,
            user=user,
            schema=_compose_schema(),
            temperature=self.temperature,
        )
        out = _ComposeOutput.model_validate(resp.data)

        agents = []
        for a in out.agents:
            agents.append(
                a.model_copy(
                    update={"agent_id": _normalize_typed_id(a.agent_id, EntityKind.AGENT)}
                )
            )

        cfg = ScenarioConfig(
            title=out.title,
            description=out.description or description,
            seed=seed,
            ticks=ticks,
            runtime=RuntimeConfig(language=language),
        )

        persona_gen = PersonaGenerator(llm=self.llm, temperature=self.temperature)

        async def _build_persona(a: _ComposeAgent) -> PersonaArtifact:
            if not self.generate_personas:
                return PersonaArtifact(persona_id=a.agent_id, summary=a.persona or "")
            artifact = await persona_gen.generate(
                agent_id=a.agent_id,
                name=a.name,
                internal=bool(a.internal),
                persona_hint=a.persona or "",
                scenario_description=cfg.description,
                language=language,
            )
            artifact.persona_id = a.agent_id
            return artifact

        personas = await asyncio.gather(*[_build_persona(a) for a in agents])

        cfg.agents = []
        for a, persona in zip(agents, personas, strict=True):
            cfg.agents.append(
                AgentConfig(
                    agent_id=a.agent_id,
                    name=a.name,
                    internal=a.internal,
                    persona=persona,
                    initial_title=a.initial_title,
                    wants_promotion=a.wants_promotion,
                    capabilities=list(a.capabilities),
                )
            )

        cfg.world.channels = [
            ChannelConfig.model_validate(
                {**x, "channel_id": _normalize_typed_id(str(x.get("channel_id") or ""), EntityKind.CHANNEL)}
            )
            for x in out.world.channels
        ]
        cfg.world.orgs = [
            OrgConfig.model_validate(
                {**x, "org_id": _normalize_typed_id(str(x.get("org_id") or ""), EntityKind.ORG)}
            )
            for x in out.world.orgs
        ]
        cfg.world.work_items = []
        for x in out.world.work_items:
            participants_raw = x.get("participants") or []
            participants = [
                _normalize_typed_id(str(pid or ""), EntityKind.AGENT)
                for pid in participants_raw
                if str(pid or "").strip()
            ]
            cfg.world.work_items.append(
                WorkItemConfig.model_validate(
                    {
                        **x,
                        "work_id": _normalize_typed_id(str(x.get("work_id") or ""), EntityKind.WORK_ITEM),
                        "participants": participants,
                    }
                )
            )
        return cfg
