from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from magistry_sim.models import Agent, Event


@dataclass(slots=True)
class World:
    agents: dict[str, Agent]
    social_graph: nx.Graph
    events: list[Event] = field(default_factory=list)

    state: dict[str, Any] = field(default_factory=dict)

    def log(self, tick: int, event_type: str, **payload: Any) -> None:
        self.events.append(Event(tick=tick, event_type=event_type, payload=dict(payload)))

