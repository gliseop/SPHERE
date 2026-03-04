"""Инкрементальный YAML-журнал мира для арбитра."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import Event
from .ids import EntityKind
from .state import WorldState


_KIND_TO_COUNT_KEY: dict[str, str] = {
    EntityKind.AGENT.value: "agents",
    EntityKind.ORG.value: "orgs",
    EntityKind.CHANNEL.value: "channels",
    EntityKind.WORK_ITEM.value: "work_items",
    EntityKind.ARTIFACT.value: "artifacts",
    EntityKind.VOTE.value: "votes",
}


@dataclass(slots=True)
class WorldJournal:
    """Компактный журнал мира, обновляемый по событиям.

    Журнал нужен арбитру для свободных действий (perform), чтобы:
    - не пересериализовывать весь `WorldState` каждый тик;
    - давать LLM компактный YAML-контекст с причинно важными фактами.
    """

    max_work_items: int = 20
    max_votes: int = 20

    tick: int = 0
    entity_counts: dict[str, int] = field(default_factory=dict)

    agent_ids: list[str] = field(default_factory=list)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)

    work_item_ids: list[str] = field(default_factory=list)
    work_items: dict[str, dict[str, Any]] = field(default_factory=dict)

    vote_ids: list[str] = field(default_factory=list)
    votes: dict[str, dict[str, Any]] = field(default_factory=dict)

    _dirty: bool = True
    _yaml_cache: str = ""

    @classmethod
    def from_state(
        cls, *, state: WorldState, max_work_items: int = 20, max_votes: int = 20
    ) -> "WorldJournal":
        j = cls(max_work_items=max_work_items, max_votes=max_votes)
        j.tick = int(state.tick)
        j.entity_counts = {
            "agents": len(state.registry.list_ids(EntityKind.AGENT)),
            "orgs": len(state.registry.list_ids(EntityKind.ORG)),
            "channels": len(state.registry.list_ids(EntityKind.CHANNEL)),
            "work_items": len(state.registry.list_ids(EntityKind.WORK_ITEM)),
            "artifacts": len(state.registry.list_ids(EntityKind.ARTIFACT)),
            "votes": len(state.registry.list_ids(EntityKind.VOTE)),
        }

        j.agent_ids = sorted(state.agents.keys())
        for aid in j.agent_ids:
            j.agents[aid] = j._agent_entry(state, aid)

        j.work_item_ids = sorted(state.work_items.keys())
        for wid in j.work_item_ids:
            j.work_items[wid] = j._work_item_entry(state, wid)

        j.vote_ids = sorted(state.votes.keys())
        for vid in j.vote_ids:
            j.votes[vid] = j._vote_entry(state, vid)

        j._dirty = True
        return j

    def set_tick(self, tick: int) -> None:
        tick = int(tick)
        if tick == self.tick:
            return
        self.tick = tick
        self._dirty = True

    def apply_events(self, *, state: WorldState, events: list[Event]) -> None:
        """Применить события (после применения ops) к журналу."""
        changed = False
        for ev in events:
            if ev.event_type == "entity_created":
                kind = str((ev.payload or {}).get("kind") or "").strip()
                key = _KIND_TO_COUNT_KEY.get(kind)
                if key:
                    self.entity_counts[key] = int(self.entity_counts.get(key, 0)) + 1
                    changed = True
                entity_id = str((ev.payload or {}).get("entity_id") or "").strip()
                if kind == EntityKind.AGENT.value and entity_id and entity_id in state.agents:
                    if entity_id not in self.agents:
                        self.agent_ids.append(entity_id)
                        self.agent_ids.sort()
                    self.agents[entity_id] = self._agent_entry(state, entity_id)
                    changed = True
                continue

            if ev.event_type == "work_item_created":
                self.entity_counts["work_items"] = int(self.entity_counts.get("work_items", 0)) + 1
                work_id = str((ev.payload or {}).get("work_id") or "").strip()
                if work_id and work_id in state.work_items:
                    if work_id not in self.work_items:
                        self.work_item_ids.append(work_id)
                        self.work_item_ids.sort()
                    self.work_items[work_id] = self._work_item_entry(state, work_id)
                changed = True
                continue

            if ev.event_type in ("work_note_added", "work_proposal_submitted"):
                work_id = str((ev.payload or {}).get("work_id") or "").strip()
                if work_id and work_id in state.work_items:
                    if work_id not in self.work_items:
                        self.work_item_ids.append(work_id)
                        self.work_item_ids.sort()
                    self.work_items[work_id] = self._work_item_entry(state, work_id)
                    changed = True
                continue

            if ev.event_type == "vote_opened":
                self.entity_counts["votes"] = int(self.entity_counts.get("votes", 0)) + 1
                vote_id = str((ev.payload or {}).get("vote_id") or "").strip()
                if vote_id and vote_id in state.votes:
                    if vote_id not in self.votes:
                        self.vote_ids.append(vote_id)
                        self.vote_ids.sort()
                    self.votes[vote_id] = self._vote_entry(state, vote_id)
                changed = True
                continue

            if ev.event_type in (
                "vote_cast",
                "vote_target_consented",
                "vote_target_declined",
                "vote_closed",
            ):
                vote_id = str((ev.payload or {}).get("vote_id") or "").strip()
                if vote_id and vote_id in state.votes:
                    if vote_id not in self.votes:
                        self.vote_ids.append(vote_id)
                        self.vote_ids.sort()
                    self.votes[vote_id] = self._vote_entry(state, vote_id)
                    changed = True
                continue

            if ev.event_type == "position_changed":
                target = str((ev.payload or {}).get("target_agent_id") or "").strip()
                if target and target in state.agents:
                    if target not in self.agents:
                        self.agent_ids.append(target)
                        self.agent_ids.sort()
                    self.agents[target] = self._agent_entry(state, target)
                    changed = True
                continue

        if changed:
            self._dirty = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "entities": {
                "agents": int(self.entity_counts.get("agents", 0)),
                "orgs": int(self.entity_counts.get("orgs", 0)),
                "channels": int(self.entity_counts.get("channels", 0)),
                "work_items": int(self.entity_counts.get("work_items", 0)),
                "artifacts": int(self.entity_counts.get("artifacts", 0)),
                "votes": int(self.entity_counts.get("votes", 0)),
            },
            "agents": [self.agents[aid] for aid in self.agent_ids],
            "work_items": [self.work_items[wid] for wid in self.work_item_ids[: self.max_work_items]],
            "votes": [self.votes[vid] for vid in self.vote_ids[: self.max_votes]],
        }

    def to_yaml(self) -> str:
        if not self._dirty:
            return self._yaml_cache
        data = self.to_dict()
        try:
            import yaml  # type: ignore
        except ImportError:
            self._yaml_cache = str(data)
            self._dirty = False
            return self._yaml_cache

        self._yaml_cache = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        self._dirty = False
        return self._yaml_cache

    @staticmethod
    def _agent_entry(state: WorldState, agent_id: str) -> dict[str, Any]:
        a = state.agents[agent_id]
        return {
            "id": a.agent_id,
            "name": a.name,
            "internal": a.internal,
            "title": a.title if a.internal else "",
            "capabilities": list(a.capabilities),
        }

    @staticmethod
    def _work_item_entry(state: WorldState, work_id: str) -> dict[str, Any]:
        w = state.work_items[work_id]
        return {
            "id": w.work_id,
            "type": w.work_type,
            "title": w.title,
            "status": w.status,
            "participants": list(w.participants),
            "notes_count": len(w.notes),
            "proposals_count": len(w.proposals),
        }

    @staticmethod
    def _vote_entry(state: WorldState, vote_id: str) -> dict[str, Any]:
        v = state.votes[vote_id]
        return {
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
        }

