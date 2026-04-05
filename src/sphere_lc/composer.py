"""WorldComposer: генерация сценария из текстового описания."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import AgentConfig, ChannelConfig, OrgConfig, RuntimeConfig, ScenarioConfig, WorkItemConfig
from .ids import EntityKind, ParsedId, ensure_kind, make_id, parse_typed_id
from .llm import LLMCaller
from .persona import PersonaArtifact, PersonaGenerator
from .prompts import render_prompt


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
                        "agent_id": {"type": "string", "minLength": 1},
                        "name": {"type": "string"},
                        "internal": {"type": "boolean"},
                        "persona": {"type": "string"},
                        "initial_title": {"type": "string"},
                        "wants_promotion": {"type": "boolean"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["message", "work", "dao", "spawn"]},
                        },
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
                            "properties": {
                                "channel_id": {"type": "string", "minLength": 1},
                                "title": {"type": "string"},
                            },
                            "required": ["channel_id"],
                        },
                    },
                    "orgs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"org_id": {"type": "string", "minLength": 1}, "title": {"type": "string"}},
                            "required": ["org_id"],
                        },
                    },
                    "work_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "work_id": {"type": "string", "minLength": 1},
                                "work_type": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "participants": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
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


def _try_normalize_typed_id(raw_id: str, kind: EntityKind) -> str | None:
    raw_id = (raw_id or "").strip()
    if not raw_id:
        return None
    if ":" not in raw_id:
        try:
            return make_id(kind, raw_id)
        except Exception:
            return None
    try:
        ensure_kind(raw_id, kind)
    except Exception:
        return None
    return raw_id


def _normalize_or_fallback(*, raw_id: str, kind: EntityKind, fallback: str) -> str:
    normalized = _try_normalize_typed_id(raw_id, kind)
    if normalized is not None:
        return normalized
    return make_id(kind, fallback)


def _unique_id(entity_id: str, kind: EntityKind, used: set[str]) -> str:
    """Убедиться, что typed ID уникален (добавляет суффикс при необходимости)."""
    try:
        ensure_kind(entity_id, kind)
    except Exception:
        entity_id = make_id(kind, "auto")

    if entity_id not in used:
        used.add(entity_id)
        return entity_id

    parsed: ParsedId = parse_typed_id(entity_id)
    base = parsed.slug
    n = 2
    while True:
        cand = make_id(kind, f"{base}_{n}")
        if cand not in used:
            used.add(cand)
            return cand
        n += 1


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
        language: str,
    ) -> ScenarioConfig:
        """Сгенерировать сценарий.

        Args:
            description: Текстовая постановка ситуации.
            ticks: Длина прогона.
            language: Язык симуляции.
        """
        system = render_prompt("composer.system", language_repr=repr(language))
        user = render_prompt("composer.user", description=description, ticks=ticks)
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

        agents: list[_ComposeAgent] = []
        used_agents: set[str] = set()
        agent_id_map: dict[str, str] = {}
        for i, a in enumerate(out.agents):
            raw = str(a.agent_id or "")
            normalized = _normalize_or_fallback(
                raw_id=raw, kind=EntityKind.AGENT, fallback=f"auto_{i+1}"
            )
            final_id = _unique_id(normalized, EntityKind.AGENT, used_agents)
            raw_str = raw.strip()
            if raw_str and raw_str not in agent_id_map:
                agent_id_map[raw_str] = final_id
            if normalized not in agent_id_map:
                agent_id_map[normalized] = final_id
            agents.append(a.model_copy(update={"agent_id": final_id}))

        cfg = ScenarioConfig(
            title=out.title,
            description=out.description or description,
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

        used_channels: set[str] = set()
        channels = []
        for i, x in enumerate(out.world.channels):
            raw_id = str(x.get("channel_id") or "")
            normalized = _normalize_or_fallback(
                raw_id=raw_id, kind=EntityKind.CHANNEL, fallback=f"auto_{i+1}"
            )
            final_id = _unique_id(normalized, EntityKind.CHANNEL, used_channels)
            channels.append(ChannelConfig.model_validate({**x, "channel_id": final_id}))
        cfg.world.channels = channels

        used_orgs: set[str] = set()
        orgs = []
        for i, x in enumerate(out.world.orgs):
            raw_id = str(x.get("org_id") or "")
            normalized = _normalize_or_fallback(
                raw_id=raw_id, kind=EntityKind.ORG, fallback=f"auto_{i+1}"
            )
            final_id = _unique_id(normalized, EntityKind.ORG, used_orgs)
            orgs.append(OrgConfig.model_validate({**x, "org_id": final_id}))
        cfg.world.orgs = orgs

        cfg.world.work_items = []
        used_work_items: set[str] = set()
        for x in out.world.work_items:
            participants_raw = x.get("participants") or []
            participants: list[str] = []
            for pid in participants_raw:
                raw_pid = str(pid or "").strip()
                if not raw_pid:
                    continue
                normalized_pid = _try_normalize_typed_id(raw_pid, EntityKind.AGENT)
                if normalized_pid is None:
                    continue
                final_pid = agent_id_map.get(raw_pid) or agent_id_map.get(normalized_pid) or normalized_pid
                if final_pid in used_agents and final_pid not in participants:
                    participants.append(final_pid)

            raw_work_id = str(x.get("work_id") or "")
            normalized_work_id = _normalize_or_fallback(
                raw_id=raw_work_id,
                kind=EntityKind.WORK_ITEM,
                fallback=f"auto_{len(cfg.world.work_items)+1}",
            )
            final_work_id = _unique_id(normalized_work_id, EntityKind.WORK_ITEM, used_work_items)
            cfg.world.work_items.append(
                WorkItemConfig.model_validate(
                    {
                        **x,
                        "work_id": final_work_id,
                        "participants": participants,
                    }
                )
            )
        return cfg
