"""Агентный слой MAGISTRY-LC (1 LLM-вызов на ход)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from pydantic import TypeAdapter, ValidationError

from .actions import Action, ActionType, actions_json_schema
from .config import RuntimeConfig
from .events import Event
from .ids import INTERNAL_AUDIENCE, PUBLIC_AUDIENCE
from .llm import LLMCaller
from .state import AgentState, WorldState


_WS_RE = re.compile(r"\s+")
_ACTION_ADAPTER = TypeAdapter(Action)


def _redact_numbers(obj):
    if isinstance(obj, dict):
        return {k: _redact_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_numbers(v) for v in obj]
    if isinstance(obj, (int, float)):
        return "<num>"
    return obj


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.strip())


@dataclass(slots=True)
class AgentRunner:
    """Runner одного агента."""

    llm: LLMCaller
    runtime: RuntimeConfig
    temperature: float = 0.0

    def _build_system(self, agent: AgentState) -> str:
        lang = self.runtime.language
        return (
            "Ты — автономный агент в симуляции организационных процессов (MAGISTRY-LC).\n"
            "Выполняй действия, опираясь на личность, наблюдения и здравый смысл.\n"
            f"ВАЖНО: отвечай строго на языке: {lang!r}.\n"
            "Возвращай только JSON, без пояснений и без markdown.\n"
        )

    def _build_user(
        self,
        *,
        agent: AgentState,
        state: WorldState,
        visible_events: list[Event],
    ) -> str:
        # Антифантомы: агенту явно показываем допустимые ID.
        agent_ids = ", ".join(sorted(state.agents.keys()))
        work_ids = ", ".join(sorted(state.work_items.keys())) or "(нет)"
        chan_ids = ", ".join(sorted(state.registry.list_ids()))  # compact enough for MVP

        # Для MVP даём события как короткие факты.
        facts = []
        for ev in visible_events[-50:]:
            # не показываем сырые числа репутации и т.п.
            facts.append(f"- [{ev.event_type}] {_redact_numbers(ev.payload)}")
        facts_text = "\n".join(facts) if facts else "- (нет)"

        # Память: summary + последние факты.
        mem = []
        if agent.memory_summary:
            mem.append(f"Сводка памяти: {agent.memory_summary}")
        if agent.memory_events:
            mem.append("Последние факты:\n" + "\n".join(f"- {x}" for x in agent.memory_events[-20:]))
        mem_text = "\n\n".join(mem) if mem else "(пусто)"

        # Инструкция по действиям.
        max_actions = self.runtime.max_actions_per_turn
        return (
            f"Раунд (tick): {state.tick}\n"
            f"Ты: {agent.name} ({agent.agent_id}).\n"
            f"Твоя должность: {agent.title if agent.internal else '(внешний)'}.\n\n"
            "Доступные сущности (используй только эти ID):\n"
            f"- Agents: {agent_ids}\n"
            f"- Work items: {work_ids}\n"
            f"- Registry: {chan_ids}\n\n"
            "Наблюдения (последние события, доступные тебе):\n"
            f"{facts_text}\n\n"
            f"Память:\n{mem_text}\n\n"
            "Сгенерируй действия на этот тик.\n"
            f"Правила:\n"
            f"- максимум {max_actions} действий\n"
            "- не выдумывай новые ID; если нужна новая организация/канал — используй request_entity\n"
            "- для свободных действий используй perform (description + target_id)\n"
        )

    async def propose_actions(
        self,
        *,
        agent: AgentState,
        state: WorldState,
        visible_events: list[Event],
    ) -> list[Action]:
        """Сгенерировать список действий агента на тик."""
        schema = actions_json_schema(max_actions=self.runtime.max_actions_per_turn)
        system = self._build_system(agent)
        user = self._build_user(agent=agent, state=state, visible_events=visible_events)

        resp = await self.llm.generate_structured(
            role="agent",
            name=agent.agent_id,
            tick=state.tick,
            system=system,
            user=user,
            schema=schema,
            temperature=self.temperature,
        )

        raw = resp.data
        if not isinstance(raw, list):
            return []

        validated: list[Action] = []
        for item in raw[: self.runtime.max_actions_per_turn]:
            try:
                validated.append(_ACTION_ADAPTER.validate_python(item))
            except ValidationError:
                continue

        return self._dedup_actions(validated)

    def _dedup_actions(self, actions: Iterable[Action]) -> list[Action]:
        """Дедуп одинаковых действий в рамках одного тика."""
        seen: set[str] = set()
        out: list[Action] = []
        for act in actions:
            key = _norm(str(act))
            if key in seen:
                continue
            seen.add(key)
            out.append(act)
        return out


def event_visible_to_agent(event: Event, agent_id: str, *, internal: bool) -> bool:
    """Проверить видимость события агенту.

    Args:
        event: Событие.
        agent_id: ID агента.
        internal: Является ли агент внутренним участником системы.
    """
    if agent_id in event.audience:
        return True
    if PUBLIC_AUDIENCE in event.audience:
        return True
    if internal and INTERNAL_AUDIENCE in event.audience:
        return True
    return False
