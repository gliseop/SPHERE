"""World generator: внешние события без утечки промптов/трасс."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .events import Event
from .ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from .llm import LLMCaller
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
    reason: str = ""


class _AgentDailyContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    where_day_starts: str = ""
    personal_pressure: str = ""
    social_encounter: str = ""
    ambient_signal: str = ""
    today_hook: str = ""
    lightweight_contacts: list[str] = Field(default_factory=list)


class _SceneHookModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    agents: list[str] = Field(default_factory=list)
    description: str
    mandatory: bool = False


@dataclass(slots=True)
class SpawnSuggestion:
    """Предложение worldgen создать нового агента."""

    slug: str
    name: str
    internal: bool
    persona_hint: str
    reason: str = ""


@dataclass(slots=True)
class AgentDailyContext:
    """Короткий личный контекст агента на тик."""

    where_day_starts: str = ""
    personal_pressure: str = ""
    social_encounter: str = ""
    ambient_signal: str = ""
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
class WorldgenOutput:
    """Нормализованный результат worldgen."""

    events: list[Event]
    spawns: list[SpawnSuggestion]
    agent_contexts: dict[str, AgentDailyContext] = field(default_factory=dict)
    scene_hooks: list[SceneHook] = field(default_factory=list)


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
                        "reason": {"type": "string"},
                    },
                    "required": ["slug", "name", "internal", "persona_hint"],
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
    base = [
        "Ты — генератор внешнего мира для симуляции организационных процессов.",
        "Ты создаёшь только фон, давление, поводы и внешние последствия.",
        "Решения и действия совершают сами агенты.",
        "ЗАПРЕЩЕНО:",
        "- описывать решение, уже принятое существующим агентом;",
        "- утверждать как факт содержание приватных сообщений;",
        "- закрывать work item текстом без детерминированного StateOp;",
        "- придумывать typed-id или ссылаться на agent:/work: как на текст мира;",
        "- писать на языке, отличном от указанного.",
        f"Пиши строго на языке: {language!r}.",
        "Ответ возвращай строго как JSON по схеме.",
    ]
    if phase == "pre":
        base.extend(
            [
                "Сгенерируй:",
                "- ограниченное число глобальных событий текущего тика;",
                "- короткие личные контексты начала дня для релевантных агентов;",
                "- необязательные scene hooks как поводы к встречам или разговорам.",
                "Личный контекст должен создавать повод для выбора, а не пересказывать уже совершённое действие.",
            ]
        )
    else:
        base.extend(
            [
                "Сгенерируй только внешние отклики на уже произошедшие процессы и, при необходимости, предложения новых акторов.",
                "Не подменяй собой журнал мира и не рассказывай за существующих агентов.",
            ]
        )
    return "\n".join(base) + "\n"


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
        elif isinstance(resp.data, dict):
            events_raw = resp.data.get("events") or resp.data.get("global_events") or []
            spawns_raw = resp.data.get("spawns") or resp.data.get("spawn_suggestions") or []
            agent_contexts_raw = resp.data.get("agent_contexts") or []
            scene_hooks_raw = resp.data.get("scene_hooks") or []
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
                today_hook=raw_ctx.today_hook.strip(),
                lightweight_contacts=[str(item).strip() for item in raw_ctx.lightweight_contacts if str(item).strip()],
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

        return WorldgenOutput(
            events=out_events,
            spawns=spawns,
            agent_contexts=agent_contexts,
            scene_hooks=scene_hooks,
        )
