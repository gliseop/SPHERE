"""World generator: внешние события без утечки промптов/трасс."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

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


@dataclass(slots=True)
class SpawnSuggestion:
    """Предложение worldgen создать нового агента."""

    slug: str
    name: str
    internal: bool
    persona_hint: str
    reason: str = ""


@dataclass(slots=True)
class WorldgenOutput:
    """Нормализованный результат worldgen: события + предложения спавна."""

    events: list[Event]
    spawns: list[SpawnSuggestion]


def _worldgen_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "events": {
                "type": "array",
                "maxItems": 3,
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
                "maxItems": 2,
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
        tick_duration_days: int = 1,
        allow_internal_spawns: bool = False,
    ) -> WorldgenOutput:
        """Сгенерировать внешние события и предложения спавна."""
        compact = []
        for ev in recent_events[-200:]:
            # World-gen видит только public/internal события (без приватных 1:1 сообщений).
            if PUBLIC_AUDIENCE not in ev.audience and INTERNAL_AUDIENCE not in ev.audience:
                continue
            payload = dict(ev.payload or {})
            # Доп. защита: даже если приватное сообщение ошибочно помечено internal/public — не передаём текст.
            if ev.event_type == "message_sent" and bool(payload.get("private", True)):
                payload.pop("text", None)
                payload["text_redacted"] = True
            compact.append({"event_type": ev.event_type, "actor_id": ev.actor_id, "payload": payload})

        system = (
            "Ты — генератор внешних событий мира для симуляции организационных процессов.\n"
            "На вход: события текущего тика (нормализованные), без промптов и внутренних мыслей.\n"
            "Сгенерируй 0–3 внешних события, которые логично следуют из ситуации.\n"
            "При необходимости предложи 0–2 новых персонажей, которые логично появляются в сюжете именно сейчас.\n"
            "Новый персонаж должен быть релевантен текущим событиям и иметь краткий persona_hint.\n"
            "Используй человеко-читаемые имена людей, а не agent:* и не машинные slug-строки.\n"
            "Не предлагай абстрактные должности вместо конкретных людей.\n"
            "Если упоминаешь даты, не противоречь канонической временной линии сценария.\n"
            f"Пиши на языке: {language!r}.\n"
            "Ответ: JSON по схеме.\n"
        )
        payload = {
            "tick": tick,
            "current_date": current_date,
            "tick_duration_days": tick_duration_days,
            "allow_internal_spawns": bool(allow_internal_spawns),
            "events": compact,
        }
        user = json.dumps(payload, ensure_ascii=False)
        resp = await self.llm.generate_structured(
            role="worldgen",
            name="world_generator",
            tick=tick,
            system=system,
            user=user,
            schema=_worldgen_schema(),
            temperature=self.temperature,
        )
        if isinstance(resp.data, list):
            events_raw = resp.data
            spawns_raw = []
        elif isinstance(resp.data, dict):
            events_raw = resp.data.get("events") or []
            spawns_raw = resp.data.get("spawns") or []
        else:
            return WorldgenOutput(events=[], spawns=[])
        out: list[Event] = []
        for item in events_raw:
            try:
                we = _WorldEventModel.model_validate(item)
            except Exception:
                continue
            audience = [PUBLIC_AUDIENCE] if we.audience == "public" else [INTERNAL_AUDIENCE]
            out.append(
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
        return WorldgenOutput(events=out, spawns=spawns)
