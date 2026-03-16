"""WorldState для MAGISTRY-LC."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .entities import EntityRegistry
from .ids import EntityKind
from .memory import AgentMemory
from .persona import PersonaArtifact


@dataclass(slots=True)
class AgentState:
    """Состояние агента (внутреннее для движка).

    Важно: числовые параметры не предназначены для прямой передачи в промпты.
    """

    agent_id: str
    name: str
    internal: bool
    persona: PersonaArtifact = field(default_factory=PersonaArtifact)
    capabilities: list[str] = field(default_factory=list)

    reputation: float = 0.0  # clamp >= 0
    reputation_frozen: bool = False
    reputation_frozen_until_tick: int | None = None
    title: str = "специалист"
    wants_promotion: bool = True
    story_state: str = ""

    memory: AgentMemory | None = None

    def __post_init__(self) -> None:
        if self.memory is None:
            self.memory = AgentMemory(agent_id=self.agent_id)


@dataclass(slots=True)
class WorkItem:
    """Универсальный объект процесса: дело/проект/задача."""

    work_id: str
    work_type: str
    title: str
    description: str = ""
    participants: list[str] = field(default_factory=list)
    status: str = "open"
    notes: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Vote:
    """Голосование DAO."""

    vote_id: str
    vote_type: str  # e.g. "position_change"
    created_by: str
    created_tick: int
    closes_tick: int

    target_agent_id: str
    new_title: str
    reason: str = ""

    voters: list[str] = field(default_factory=list)
    votes: dict[str, str] = field(default_factory=dict)  # agent_id -> yes/no/abstain

    target_consented: bool | None = None
    status: str = "open"  # open|closed
    result: str | None = None  # passed|failed|canceled
    result_reason: str = ""


@dataclass(slots=True)
class WorldState:
    """Глобальное состояние мира."""

    tick: int = 0
    registry: EntityRegistry = field(default_factory=EntityRegistry)
    agents: dict[str, AgentState] = field(default_factory=dict)
    work_items: dict[str, WorkItem] = field(default_factory=dict)
    votes: dict[str, Vote] = field(default_factory=dict)

    def get_internal_agent_ids(self) -> list[str]:
        """Список внутренних агентов."""
        return sorted([aid for aid, a in self.agents.items() if a.internal])

    def clamp_reputation(self) -> None:
        """Применить инвариант репутации: reputation >= 0."""
        for a in self.agents.values():
            if a.reputation < 0:
                a.reputation = 0.0

    def journal_dict(self, *, max_work_items: int = 20, max_votes: int = 20) -> dict[str, Any]:
        """Собрать компактный YAML-журнал мира (как словарь).

        Журнал должен быть достаточно компактным, чтобы помещаться в промпт арбитра,
        и при этом отражать причинно важные факты.
        """
        agents = []
        for aid in sorted(self.agents.keys()):
            a = self.agents[aid]
            agents.append(
                {
                    "id": a.agent_id,
                    "name": a.name,
                    "internal": a.internal,
                    "title": a.title if a.internal else "",
                    "reputation": round(float(a.reputation), 3) if a.internal else None,
                    "reputation_frozen": bool(a.reputation_frozen) if a.internal else None,
                    "reputation_frozen_until_tick": a.reputation_frozen_until_tick if a.internal else None,
                    "capabilities": list(a.capabilities),
                }
            )

        work_items = []
        for wid in sorted(self.work_items.keys())[:max_work_items]:
            w = self.work_items[wid]
            work_items.append(
                {
                    "id": w.work_id,
                    "type": w.work_type,
                    "title": w.title,
                    "status": w.status,
                    "participants": list(w.participants),
                    "notes_count": len(w.notes),
                    "proposals_count": len(w.proposals),
                }
            )

        votes = []
        for vid in sorted(self.votes.keys())[:max_votes]:
            v = self.votes[vid]
            votes.append(
                {
                    "id": v.vote_id,
                    "type": v.vote_type,
                    "status": v.status,
                    "created_tick": v.created_tick,
                    "closes_tick": v.closes_tick,
                    "target_agent_id": v.target_agent_id,
                    "new_title": v.new_title,
                    "target_consented": v.target_consented,
                    "votes": dict(v.votes),
                    "result": v.result,
                    "result_reason": v.result_reason,
                }
            )

        return {
            "tick": self.tick,
            "entities": {
                "agents": len(self.registry.list_ids(EntityKind.AGENT)),
                "orgs": len(self.registry.list_ids(EntityKind.ORG)),
                "channels": len(self.registry.list_ids(EntityKind.CHANNEL)),
                "work_items": len(self.registry.list_ids(EntityKind.WORK_ITEM)),
                "artifacts": len(self.registry.list_ids(EntityKind.ARTIFACT)),
                "votes": len(self.registry.list_ids(EntityKind.VOTE)),
            },
            "agents": agents,
            "work_items": work_items,
            "votes": votes,
        }

    def journal_yaml(self) -> str:
        """Вернуть YAML-журнал мира."""
        data = self.journal_dict()
        try:
            import yaml  # type: ignore
        except ImportError:
            # Фолбэк: JSON-подобный текст. В runtime лучше установить pyyaml.
            return str(data)
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
