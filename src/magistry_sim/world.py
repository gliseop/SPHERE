from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import networkx as nx

from magistry_sim.models import Agent, Event

if TYPE_CHECKING:
    from magistry_sim.memory import AgentMemory


@dataclass(slots=True)
class World:
    """
    Состояние мира симуляции.

    Содержит:
    - agents: все агенты (Officials, Contractors)
    - social_graph: граф социальных связей
    - events: глобальный лог событий
    - agent_memories: память каждого агента (по архитектуре Codex)
    - state: произвольное состояние симуляции
    - on_event: необязательный обратный вызов при каждом событии
    """

    agents: dict[str, Agent]
    social_graph: nx.Graph
    events: list[Event] = field(default_factory=list)
    agent_memories: dict[str, "AgentMemory"] = field(default_factory=dict)

    state: dict[str, Any] = field(default_factory=dict)
    on_event: Callable[[Event], None] | None = None

    def log(self, tick: int, event_type: str, **payload: Any) -> None:
        """Записать событие в глобальный лог."""
        event = Event(tick=tick, event_type=event_type, payload=dict(payload))
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def get_memory(self, agent_id: str) -> "AgentMemory":
        """
        Получить память агента (создаёт если не существует).

        Ленивая инициализация — память создаётся при первом обращении.
        """
        from magistry_sim.memory import AgentMemory

        if agent_id not in self.agent_memories:
            self.agent_memories[agent_id] = AgentMemory(agent_id)
        return self.agent_memories[agent_id]

    def has_memory(self, agent_id: str) -> bool:
        """Проверить, есть ли память у агента."""
        return agent_id in self.agent_memories
