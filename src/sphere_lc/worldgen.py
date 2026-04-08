"""World generator: внешние события без утечки промптов/трасс."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .events import Event
from .ids import EntityKind, make_id, normalize_slug, parse_typed_id
from .ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from .llm import LLMCaller
from .prompts import render_prompt
from .utils import normalize_agent_display_name


class _WorldEventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str  # "public" | "internal"
    description: str


class _SpawnSuggestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    internal: bool
    persona_hint: str
    org_id: str = ""
    zone_id: str = ""
    reason: str = ""


class _AgentDailyContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    where_day_starts: str = ""
    personal_pressure: str = ""
    social_encounter: str = ""
    ambient_signal: str = ""
    private_pressure: str = ""
    opportunity: str = ""
    exposure_risk: str = ""
    today_hook: str = ""
    lightweight_contacts: list[str] = Field(default_factory=list)


class _SceneHookModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    agents: list[str] = Field(default_factory=list)
    description: str
    mandatory: bool = False


class _InstitutionUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: str
    operating_mode: str | None = None
    transparency_mode: str | None = None
    access_mode: str | None = None
    security_mode: str | None = None
    capture_risk: str | None = None
    linked_zone_ids: list[str] | None = None


class _ZoneUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_id: str
    access_mode: str | None = None
    transparency_mode: str | None = None
    security_level: str | None = None


class _ResourcePoolUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    quantity: float | None = None
    status: str | None = None
    pressure: str | None = None


class _InformationClimateUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_mood: str | None = None
    oversight_attention: str | None = None
    media_pressure: str | None = None
    narrative_temperature: str | None = None
    active_signals: list[str] | None = None


class _InformalLinkUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_a_id: str
    agent_b_id: str
    link_type: str
    strength: float | None = None
    visibility: str | None = None
    pressure: str | None = None
    source: str | None = None


class _EntityCreationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    kind: str
    title: str
    description: str = ""
    zone_type: str | None = None
    primary_org_id: str | None = None
    owner_org_id: str | None = None
    unit: str | None = None


class _ArtifactCreationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_type: str
    title: str
    summary: str = ""
    owner_org_id: str | None = None
    zone_id: str | None = None
    related_work_id: str | None = None
    visibility: str | None = None
    status: str | None = None
    tags: list[str] | None = None


class _ArtifactUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    title: str | None = None
    summary: str | None = None
    visibility: str | None = None
    status: str | None = None
    tags: list[str] | None = None


@dataclass(slots=True)
class SpawnSuggestion:
    """Предложение worldgen создать нового агента."""

    slug: str
    name: str
    internal: bool
    persona_hint: str
    org_id: str | None = None
    zone_id: str | None = None
    reason: str = ""


@dataclass(slots=True)
class AgentDailyContext:
    """Короткий личный контекст агента на тик."""

    where_day_starts: str = ""
    personal_pressure: str = ""
    social_encounter: str = ""
    ambient_signal: str = ""
    private_pressure: str = ""
    opportunity: str = ""
    exposure_risk: str = ""
    today_hook: str = ""
    lightweight_contacts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SceneHook:
    """Опциональный повод к взаимодействию на текущий тик."""

    kind: str
    agents: list[str]
    description: str
    mandatory: bool = False


@dataclass(slots=True)
class InstitutionUpdate:
    """Изменение режима организации в среде."""

    org_id: str
    operating_mode: str | None = None
    transparency_mode: str | None = None
    access_mode: str | None = None
    security_mode: str | None = None
    capture_risk: str | None = None
    linked_zone_ids: list[str] | None = None


@dataclass(slots=True)
class ZoneUpdate:
    """Изменение режима зоны."""

    zone_id: str
    access_mode: str | None = None
    transparency_mode: str | None = None
    security_level: str | None = None


@dataclass(slots=True)
class ResourcePoolUpdate:
    """Изменение ресурсного контура."""

    resource_id: str
    quantity: float | None = None
    status: str | None = None
    pressure: str | None = None


@dataclass(slots=True)
class InformationClimateUpdate:
    """Изменение глобального информационного климата."""

    public_mood: str | None = None
    oversight_attention: str | None = None
    media_pressure: str | None = None
    narrative_temperature: str | None = None
    active_signals: list[str] | None = None


@dataclass(slots=True)
class InformalLinkUpdate:
    """Изменение неформальной связи."""

    agent_a_id: str
    agent_b_id: str
    link_type: str
    strength: float | None = None
    visibility: str | None = None
    pressure: str | None = None
    source: str | None = None


@dataclass(slots=True)
class EnvironmentUpdates:
    """Пакет средовых изменений от worldgen."""

    institutions: list[InstitutionUpdate] = field(default_factory=list)
    zones: list[ZoneUpdate] = field(default_factory=list)
    resource_pools: list[ResourcePoolUpdate] = field(default_factory=list)
    information_climate: InformationClimateUpdate | None = None
    informal_links: list[InformalLinkUpdate] = field(default_factory=list)


@dataclass(slots=True)
class EntityCreation:
    """Предложение worldgen создать новую сущность мира."""

    entity_id: str
    kind: str
    title: str
    description: str = ""
    zone_type: str | None = None
    primary_org_id: str | None = None
    owner_org_id: str | None = None
    unit: str | None = None


@dataclass(slots=True)
class ArtifactCreation:
    """Предложение создать новый документ/артефакт."""

    artifact_id: str
    artifact_type: str
    title: str
    summary: str = ""
    owner_org_id: str | None = None
    zone_id: str | None = None
    related_work_id: str | None = None
    visibility: str | None = None
    status: str | None = None
    tags: list[str] | None = None


@dataclass(slots=True)
class ArtifactUpdate:
    """Предложение обновить существующий документ/артефакт."""

    artifact_id: str
    title: str | None = None
    summary: str | None = None
    visibility: str | None = None
    status: str | None = None
    tags: list[str] | None = None


@dataclass(slots=True)
class WorldgenOutput:
    """Нормализованный результат worldgen.

    Контракт: `entity_creations` должны материализоваться до применения
    `artifact_creations`, чтобы внешние `owner_org_id` / `zone_id` уже
    существовали в реестре или были созданы в этом же проходе worldgen.
    """

    events: list[Event]
    spawns: list[SpawnSuggestion]
    agent_contexts: dict[str, AgentDailyContext] = field(default_factory=dict)
    scene_hooks: list[SceneHook] = field(default_factory=list)
    environment_updates: EnvironmentUpdates = field(default_factory=EnvironmentUpdates)
    entity_creations: list[EntityCreation] = field(default_factory=list)
    artifact_creations: list[ArtifactCreation] = field(default_factory=list)
    artifact_updates: list[ArtifactUpdate] = field(default_factory=list)


def normalize_worldgen_artifact_id(raw_id: Any) -> str:
    """Привести worldgen artifact ID к каноническому `art:*` виду."""

    artifact_id = str(raw_id or "").strip()
    if not artifact_id:
        return ""
    if artifact_id.startswith("artifact:"):
        artifact_id = f"art:{artifact_id.split(':', 1)[1]}"
    try:
        parsed = parse_typed_id(artifact_id)
    except ValueError:
        slug = artifact_id.split(":", 1)[1] if ":" in artifact_id else artifact_id
        return make_id(EntityKind.ARTIFACT, normalize_slug(slug, fallback="artifact"))
    if parsed.kind == EntityKind.ARTIFACT:
        return artifact_id
    return make_id(EntityKind.ARTIFACT, normalize_slug(parsed.slug, fallback="artifact"))


@dataclass(slots=True)
class ArtifactDependencyIssue:
    """Диагностическая запись о неразрешённой зависимости артефакта."""

    artifact_id: str
    missing_owner_org_id: str | None = None
    missing_zone_id: str | None = None


def collect_artifact_dependency_issues(
    *,
    known_entity_ids: set[str],
    artifact_creations: list[ArtifactCreation],
) -> list[ArtifactDependencyIssue]:
    """Найти артефакты, которые ссылаются на отсутствующие внешние сущности.

    Артефакт допускается только тогда, когда его `owner_org_id` и `zone_id`
    уже существуют в `known_entity_ids`. Это позволяет worldgen сначала
    материализовать внешнюю инфраструктуру через `entity_creations`, а уже
    потом опираться на неё в документах.
    """

    available_entity_ids = {entity_id.strip() for entity_id in known_entity_ids if entity_id.strip()}
    issues: list[ArtifactDependencyIssue] = []
    for item in artifact_creations:
        artifact_id = normalize_worldgen_artifact_id(item.artifact_id)
        if not artifact_id:
            continue
        missing_owner_org_id = None
        if item.owner_org_id is not None:
            owner_org_id = item.owner_org_id.strip()
            if owner_org_id and owner_org_id not in available_entity_ids:
                missing_owner_org_id = owner_org_id
        missing_zone_id = None
        if item.zone_id is not None:
            zone_id = item.zone_id.strip()
            if zone_id and zone_id not in available_entity_ids:
                missing_zone_id = zone_id
        if missing_owner_org_id is None and missing_zone_id is None:
            continue
        issues.append(
            ArtifactDependencyIssue(
                artifact_id=artifact_id,
                missing_owner_org_id=missing_owner_org_id,
                missing_zone_id=missing_zone_id,
            )
        )
    return issues


def _worldgen_schema(
    *,
    phase: Literal["pre", "post"],
    event_budget: int,
    max_new_actors: int,
    max_scene_changes: int,
    agent_context_budget: int,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "events": {
                "type": "array",
                "maxItems": max(0, event_budget),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "audience": {"type": "string", "enum": ["public", "internal"]},
                        "description": {"type": "string"},
                    },
                    "required": ["audience", "description"],
                },
            },
            "spawns": {
                "type": "array",
                "maxItems": max(0, max_new_actors),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "slug": {"type": "string"},
                        "name": {"type": "string"},
                        "internal": {"type": "boolean"},
                        "persona_hint": {"type": "string"},
                        "org_id": {"type": "string"},
                        "zone_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["slug", "name", "internal", "persona_hint"],
                },
            },
            "environment_updates": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "institutions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "org_id": {"type": "string"},
                                "operating_mode": {"type": ["string", "null"]},
                                "transparency_mode": {"type": ["string", "null"]},
                                "access_mode": {"type": ["string", "null"]},
                                "security_mode": {"type": ["string", "null"]},
                                "capture_risk": {"type": ["string", "null"]},
                                "linked_zone_ids": {
                                    "type": ["array", "null"],
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["org_id"],
                        },
                    },
                    "zones": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "zone_id": {"type": "string"},
                                "access_mode": {"type": ["string", "null"]},
                                "transparency_mode": {"type": ["string", "null"]},
                                "security_level": {"type": ["string", "null"]},
                            },
                            "required": ["zone_id"],
                        },
                    },
                    "resource_pools": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "resource_id": {"type": "string"},
                                "quantity": {"type": ["number", "null"]},
                                "status": {"type": ["string", "null"]},
                                "pressure": {"type": ["string", "null"]},
                            },
                            "required": ["resource_id"],
                        },
                    },
                    "information_climate": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "public_mood": {"type": ["string", "null"]},
                            "oversight_attention": {"type": ["string", "null"]},
                            "media_pressure": {"type": ["string", "null"]},
                            "narrative_temperature": {"type": ["string", "null"]},
                            "active_signals": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                        },
                    },
                    "informal_links": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "agent_a_id": {"type": "string"},
                                "agent_b_id": {"type": "string"},
                                "link_type": {"type": "string"},
                                "strength": {"type": ["number", "null"]},
                                "visibility": {"type": ["string", "null"]},
                                "pressure": {"type": ["string", "null"]},
                                "source": {"type": ["string", "null"]},
                            },
                            "required": ["agent_a_id", "agent_b_id", "link_type"],
                        },
                    },
                },
            },
            "entity_creations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "entity_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["org", "chan", "zone", "res"]},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "zone_type": {"type": ["string", "null"]},
                        "primary_org_id": {"type": ["string", "null"]},
                        "owner_org_id": {"type": ["string", "null"]},
                        "unit": {"type": ["string", "null"]},
                    },
                    "required": ["entity_id", "kind", "title"],
                },
            },
            "artifact_creations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "artifact_type": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "owner_org_id": {"type": ["string", "null"]},
                        "zone_id": {"type": ["string", "null"]},
                        "related_work_id": {"type": ["string", "null"]},
                        "visibility": {"type": ["string", "null"]},
                        "status": {"type": ["string", "null"]},
                        "tags": {"type": ["array", "null"], "items": {"type": "string"}},
                    },
                    "required": ["artifact_id", "artifact_type", "title"],
                },
            },
            "artifact_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "title": {"type": ["string", "null"]},
                        "summary": {"type": ["string", "null"]},
                        "visibility": {"type": ["string", "null"]},
                        "status": {"type": ["string", "null"]},
                        "tags": {"type": ["array", "null"], "items": {"type": "string"}},
                    },
                    "required": ["artifact_id"],
                },
            },
        },
        "required": ["events"],
    }
    if phase == "pre":
        schema["properties"]["agent_contexts"] = {
            "type": "array",
            "maxItems": max(0, agent_context_budget),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "agent_id": {"type": "string"},
                    "where_day_starts": {"type": "string"},
                    "personal_pressure": {"type": "string"},
                    "social_encounter": {"type": "string"},
                    "ambient_signal": {"type": "string"},
                    "private_pressure": {"type": "string"},
                    "opportunity": {"type": "string"},
                    "exposure_risk": {"type": "string"},
                    "today_hook": {"type": "string"},
                    "lightweight_contacts": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["agent_id"],
            },
        }
        schema["properties"]["scene_hooks"] = {
            "type": "array",
            "maxItems": max(0, max_scene_changes),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "description": {"type": "string"},
                    "mandatory": {"type": "boolean"},
                },
                "required": ["kind", "agents", "description"],
            },
        }
    return schema


def _compact_recent_events(recent_events: list[Event]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    compact: list[dict[str, Any]] = []
    private_signals: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in recent_events[-200:]:
        if PUBLIC_AUDIENCE not in ev.audience and INTERNAL_AUDIENCE not in ev.audience:
            if ev.event_type == "message_sent" and bool((ev.payload or {}).get("private", True)):
                actor_id = str(ev.actor_id or "")
                to_id = str((ev.payload or {}).get("to_id") or "")
                if actor_id and to_id:
                    key = tuple(sorted([actor_id, to_id]))
                    signal = private_signals.setdefault(
                        key,
                        {
                            "agents": list(key),
                            "kind": "recent_private_contact",
                            "count": 0,
                        },
                    )
                    signal["count"] = int(signal["count"]) + 1
            continue

        payload = dict(ev.payload or {})
        if ev.event_type == "message_sent" and bool(payload.get("private", True)):
            payload.pop("text", None)
            payload["text_redacted"] = True
        compact.append(
            {
                "event_type": ev.event_type,
                "actor_id": ev.actor_id,
                "payload": payload,
            }
        )
    return compact, list(private_signals.values())


def _build_system_prompt(*, phase: Literal["pre", "post"], language: str) -> str:
    key = "worldgen.system.pre" if phase == "pre" else "worldgen.system.post"
    return render_prompt(key, language_repr=repr(language)) + "\n"


@dataclass(slots=True)
class WorldGenerator:
    """LLM-генератор внешних событий мира."""

    llm: LLMCaller
    temperature: float = 0.0

    async def generate(
        self,
        *,
        tick: int,
        recent_events: list[Event],
        language: str,
        current_date: str | None = None,
        current_time: str | None = None,
        tick_duration_days: int = 1,
        tick_granularity: str = "day",
        allow_internal_spawns: bool = False,
        phase: Literal["pre", "post"] = "post",
        scenario_description: str = "",
        state_snapshot: dict[str, Any] | None = None,
        agent_briefs: list[dict[str, Any]] | None = None,
        worldgen_event_budget_per_tick: int = 3,
        agent_context_budget_per_tick: int = 0,
        max_scene_changes_per_tick: int = 0,
        max_new_actors_per_window: int = 2,
    ) -> WorldgenOutput:
        """Сгенерировать материал внешнего мира."""
        compact, private_signals = _compact_recent_events(recent_events)
        payload = {
            "phase": phase,
            "tick": tick,
            "current_date": current_date,
            "current_time": current_time,
            "tick_granularity": tick_granularity,
            "tick_duration_days": tick_duration_days,
            "allow_internal_spawns": bool(allow_internal_spawns),
            "scenario_description": scenario_description,
            "state_snapshot": state_snapshot or {},
            "agent_briefs": agent_briefs or [],
            "recent_private_signals": private_signals,
            "events": compact,
        }
        user = json.dumps(payload, ensure_ascii=False)
        resp = await self.llm.generate_structured(
            role="worldgen",
            name=f"world_generator_{phase}",
            tick=tick,
            system=_build_system_prompt(phase=phase, language=language),
            user=user,
            schema=_worldgen_schema(
                phase=phase,
                event_budget=worldgen_event_budget_per_tick,
                max_new_actors=max_new_actors_per_window,
                max_scene_changes=max_scene_changes_per_tick,
                agent_context_budget=agent_context_budget_per_tick,
            ),
            temperature=self.temperature,
        )

        if isinstance(resp.data, list):
            events_raw = resp.data
            spawns_raw = []
            agent_contexts_raw: list[dict[str, Any]] | dict[str, Any] = []
            scene_hooks_raw: list[dict[str, Any]] = []
            environment_updates_raw: dict[str, Any] = {}
            entity_creations_raw: list[dict[str, Any]] = []
            artifact_creations_raw: list[dict[str, Any]] = []
            artifact_updates_raw: list[dict[str, Any]] = []
        elif isinstance(resp.data, dict):
            events_raw = resp.data.get("events") or resp.data.get("global_events") or []
            spawns_raw = resp.data.get("spawns") or resp.data.get("spawn_suggestions") or []
            agent_contexts_raw = resp.data.get("agent_contexts") or []
            scene_hooks_raw = resp.data.get("scene_hooks") or []
            environment_updates_raw = resp.data.get("environment_updates") or {}
            entity_creations_raw = resp.data.get("entity_creations") or []
            artifact_creations_raw = resp.data.get("artifact_creations") or []
            artifact_updates_raw = resp.data.get("artifact_updates") or []
        else:
            return WorldgenOutput(events=[], spawns=[])

        out_events: list[Event] = []
        for item in events_raw:
            try:
                we = _WorldEventModel.model_validate(item)
            except Exception:
                continue
            audience = [PUBLIC_AUDIENCE] if we.audience == "public" else [INTERNAL_AUDIENCE]
            out_events.append(
                Event(
                    tick=tick,
                    event_type="world_event",
                    actor_id=None,
                    payload={"description": we.description},
                    audience=audience,
                )
            )

        spawns: list[SpawnSuggestion] = []
        for item in spawns_raw:
            try:
                spawn = _SpawnSuggestionModel.model_validate(item)
            except Exception:
                continue
            display_name = normalize_agent_display_name(spawn.name, fallback=spawn.slug)
            if not spawn.slug.strip() or not display_name or not spawn.persona_hint.strip():
                continue
            if spawn.internal and not allow_internal_spawns:
                continue
            spawns.append(
                SpawnSuggestion(
                    slug=spawn.slug.strip(),
                    name=display_name,
                    internal=bool(spawn.internal),
                    persona_hint=spawn.persona_hint.strip(),
                    org_id=(spawn.org_id or "").strip() or None,
                    zone_id=(spawn.zone_id or "").strip() or None,
                    reason=spawn.reason.strip(),
                )
            )

        agent_contexts: dict[str, AgentDailyContext] = {}
        if isinstance(agent_contexts_raw, dict):
            normalized: list[dict[str, Any]] = []
            for agent_id, item in agent_contexts_raw.items():
                if not isinstance(item, dict):
                    continue
                normalized.append({"agent_id": agent_id, **item})
            agent_contexts_iterable: list[dict[str, Any]] = normalized
        elif isinstance(agent_contexts_raw, list):
            agent_contexts_iterable = [item for item in agent_contexts_raw if isinstance(item, dict)]
        else:
            agent_contexts_iterable = []
        for item in agent_contexts_iterable:
            try:
                raw_ctx = _AgentDailyContextModel.model_validate(item)
            except Exception:
                continue
            agent_id = raw_ctx.agent_id.strip()
            if not agent_id:
                continue
            agent_contexts[agent_id] = AgentDailyContext(
                where_day_starts=raw_ctx.where_day_starts.strip(),
                personal_pressure=raw_ctx.personal_pressure.strip(),
                social_encounter=raw_ctx.social_encounter.strip(),
                ambient_signal=raw_ctx.ambient_signal.strip(),
                private_pressure=raw_ctx.private_pressure.strip(),
                opportunity=raw_ctx.opportunity.strip(),
                exposure_risk=raw_ctx.exposure_risk.strip(),
                today_hook=raw_ctx.today_hook.strip(),
                lightweight_contacts=[
                    str(item).strip()
                    for item in raw_ctx.lightweight_contacts
                    if str(item).strip() and ":" not in str(item)
                ],
            )

        scene_hooks: list[SceneHook] = []
        if isinstance(scene_hooks_raw, list):
            for item in scene_hooks_raw:
                try:
                    raw_hook = _SceneHookModel.model_validate(item)
                except Exception:
                    continue
                hook_agents = [str(agent_id).strip() for agent_id in raw_hook.agents if str(agent_id).strip()]
                if not raw_hook.kind.strip() or not raw_hook.description.strip():
                    continue
                scene_hooks.append(
                    SceneHook(
                        kind=raw_hook.kind.strip(),
                        agents=hook_agents,
                        description=raw_hook.description.strip(),
                        mandatory=bool(raw_hook.mandatory),
                    )
                )

        environment_updates = EnvironmentUpdates()
        if isinstance(environment_updates_raw, dict):
            institutions_raw = environment_updates_raw.get("institutions") or []
            if isinstance(institutions_raw, list):
                for item in institutions_raw:
                    try:
                        raw = _InstitutionUpdateModel.model_validate(item)
                    except Exception:
                        continue
                    org_id = raw.org_id.strip()
                    if not org_id:
                        continue
                    linked_zone_ids = None
                    if isinstance(raw.linked_zone_ids, list):
                        linked_zone_ids = [str(zone_id).strip() for zone_id in raw.linked_zone_ids if str(zone_id).strip()]
                    environment_updates.institutions.append(
                        InstitutionUpdate(
                            org_id=org_id,
                            operating_mode=(raw.operating_mode or "").strip() or None,
                            transparency_mode=(raw.transparency_mode or "").strip() or None,
                            access_mode=(raw.access_mode or "").strip() or None,
                            security_mode=(raw.security_mode or "").strip() or None,
                            capture_risk=(raw.capture_risk or "").strip() or None,
                            linked_zone_ids=linked_zone_ids,
                        )
                    )

            zones_raw = environment_updates_raw.get("zones") or []
            if isinstance(zones_raw, list):
                for item in zones_raw:
                    try:
                        raw = _ZoneUpdateModel.model_validate(item)
                    except Exception:
                        continue
                    zone_id = raw.zone_id.strip()
                    if not zone_id:
                        continue
                    environment_updates.zones.append(
                        ZoneUpdate(
                            zone_id=zone_id,
                            access_mode=(raw.access_mode or "").strip() or None,
                            transparency_mode=(raw.transparency_mode or "").strip() or None,
                            security_level=(raw.security_level or "").strip() or None,
                        )
                    )

            resource_pools_raw = environment_updates_raw.get("resource_pools") or []
            if isinstance(resource_pools_raw, list):
                for item in resource_pools_raw:
                    try:
                        raw = _ResourcePoolUpdateModel.model_validate(item)
                    except Exception:
                        continue
                    resource_id = raw.resource_id.strip()
                    if not resource_id:
                        continue
                    environment_updates.resource_pools.append(
                        ResourcePoolUpdate(
                            resource_id=resource_id,
                            quantity=float(raw.quantity) if raw.quantity is not None else None,
                            status=(raw.status or "").strip() or None,
                            pressure=(raw.pressure or "").strip() or None,
                        )
                    )

            climate_raw = environment_updates_raw.get("information_climate")
            if isinstance(climate_raw, dict):
                try:
                    raw_climate = _InformationClimateUpdateModel.model_validate(climate_raw)
                except Exception:
                    raw_climate = None
                if raw_climate is not None:
                    active_signals = None
                    if isinstance(raw_climate.active_signals, list):
                        active_signals = [str(item).strip() for item in raw_climate.active_signals if str(item).strip()]
                    environment_updates.information_climate = InformationClimateUpdate(
                        public_mood=(raw_climate.public_mood or "").strip() or None,
                        oversight_attention=(raw_climate.oversight_attention or "").strip() or None,
                        media_pressure=(raw_climate.media_pressure or "").strip() or None,
                        narrative_temperature=(raw_climate.narrative_temperature or "").strip() or None,
                        active_signals=active_signals,
                    )

            informal_links_raw = environment_updates_raw.get("informal_links") or []
            if isinstance(informal_links_raw, list):
                for item in informal_links_raw:
                    try:
                        raw = _InformalLinkUpdateModel.model_validate(item)
                    except Exception:
                        continue
                    agent_a_id = raw.agent_a_id.strip()
                    agent_b_id = raw.agent_b_id.strip()
                    link_type = raw.link_type.strip()
                    if not agent_a_id or not agent_b_id or not link_type or agent_a_id == agent_b_id:
                        continue
                    environment_updates.informal_links.append(
                        InformalLinkUpdate(
                            agent_a_id=agent_a_id,
                            agent_b_id=agent_b_id,
                            link_type=link_type,
                            strength=float(raw.strength) if raw.strength is not None else None,
                            visibility=(raw.visibility or "").strip() or None,
                            pressure=(raw.pressure or "").strip() or None,
                            source=(raw.source or "").strip() or None,
                        )
                    )

        artifact_creations: list[ArtifactCreation] = []
        entity_creations: list[EntityCreation] = []
        if isinstance(entity_creations_raw, list):
            for item in entity_creations_raw:
                try:
                    raw = _EntityCreationModel.model_validate(item)
                except Exception:
                    continue
                entity_id = raw.entity_id.strip()
                kind = raw.kind.strip()
                title = raw.title.strip()
                if not entity_id or not kind or not title:
                    continue
                entity_creations.append(
                    EntityCreation(
                        entity_id=entity_id,
                        kind=kind,
                        title=title,
                        description=(raw.description or "").strip(),
                        zone_type=(raw.zone_type or "").strip() or None,
                        primary_org_id=(raw.primary_org_id or "").strip() or None,
                        owner_org_id=(raw.owner_org_id or "").strip() or None,
                        unit=(raw.unit or "").strip() or None,
                    )
                )
        if isinstance(artifact_creations_raw, list):
            for item in artifact_creations_raw:
                try:
                    raw = _ArtifactCreationModel.model_validate(item)
                except Exception:
                    continue
                artifact_id = raw.artifact_id.strip()
                artifact_type = raw.artifact_type.strip()
                title = raw.title.strip()
                if not artifact_id or not artifact_type or not title:
                    continue
                artifact_creations.append(
                    ArtifactCreation(
                        artifact_id=artifact_id,
                        artifact_type=artifact_type,
                        title=title,
                        summary=(raw.summary or "").strip(),
                        owner_org_id=(raw.owner_org_id or "").strip() or None,
                        zone_id=(raw.zone_id or "").strip() or None,
                        related_work_id=(raw.related_work_id or "").strip() or None,
                        visibility=(raw.visibility or "").strip() or None,
                        status=(raw.status or "").strip() or None,
                        tags=[str(tag).strip() for tag in raw.tags or [] if str(tag).strip()] if raw.tags is not None else None,
                    )
                )

        artifact_updates: list[ArtifactUpdate] = []
        if isinstance(artifact_updates_raw, list):
            for item in artifact_updates_raw:
                try:
                    raw = _ArtifactUpdateModel.model_validate(item)
                except Exception:
                    continue
                artifact_id = raw.artifact_id.strip()
                if not artifact_id:
                    continue
                artifact_updates.append(
                    ArtifactUpdate(
                        artifact_id=artifact_id,
                        title=(raw.title or "").strip() or None,
                        summary=(raw.summary or "").strip() or None,
                        visibility=(raw.visibility or "").strip() or None,
                        status=(raw.status or "").strip() or None,
                        tags=[str(tag).strip() for tag in raw.tags or [] if str(tag).strip()] if raw.tags is not None else None,
                    )
                )

        return WorldgenOutput(
            events=out_events,
            spawns=spawns,
            agent_contexts=agent_contexts,
            scene_hooks=scene_hooks,
            environment_updates=environment_updates,
            entity_creations=entity_creations,
            artifact_creations=artifact_creations,
            artifact_updates=artifact_updates,
        )
