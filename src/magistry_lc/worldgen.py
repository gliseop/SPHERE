"""World generator: внешние события без утечки промптов/трасс."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from .events import Event
from .ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from .llm import LLMCaller


class _WorldEventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str  # "public" | "internal"
    description: str


def _worldgen_schema() -> dict[str, Any]:
    return {
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
    }


@dataclass(slots=True)
class WorldGenerator:
    """LLM-генератор внешних событий мира."""

    llm: LLMCaller
    temperature: float = 0.0

    async def generate(self, *, tick: int, recent_events: list[Event], language: str) -> list[Event]:
        """Сгенерировать внешние события на основе нормализованных событий раунда."""
        compact = []
        for ev in recent_events[-200:]:
            compact.append({"event_type": ev.event_type, "actor_id": ev.actor_id, "payload": ev.payload})

        system = (
            "Ты — генератор внешних событий мира для симуляции организационных процессов.\n"
            "На вход: события текущего тика (нормализованные), без промптов и внутренних мыслей.\n"
            "Сгенерируй 0–3 внешних события, которые логично следуют из ситуации.\n"
            f"Пиши на языке: {language!r}.\n"
            "Ответ: JSON по схеме.\n"
        )
        user = json.dumps({"tick": tick, "events": compact}, ensure_ascii=False)
        resp = await self.llm.generate_structured(
            role="worldgen",
            name="world_generator",
            tick=tick,
            system=system,
            user=user,
            schema=_worldgen_schema(),
            temperature=self.temperature,
        )
        if not isinstance(resp.data, list):
            return []
        out: list[Event] = []
        for item in resp.data:
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
        return out

