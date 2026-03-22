"""Инкрементальный YAML-журнал мира для арбитра."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import Event
from .ids import EntityKind
from .state import WorldState

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


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
    store_max_work_items: int = 200
    store_max_votes: int = 200
    history_max_entries: int = 60

    tick: int = 0
    entity_counts: dict[str, int] = field(default_factory=dict)

    agent_ids: list[str] = field(default_factory=list)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)

    work_item_order: list[str] = field(default_factory=list)
    work_items: dict[str, dict[str, Any]] = field(default_factory=dict)

    vote_order: list[str] = field(default_factory=list)
    votes: dict[str, dict[str, Any]] = field(default_factory=dict)

    history: list[dict[str, Any]] = field(default_factory=list)

    _dirty: bool = True
    _yaml_cache: str = ""

    @classmethod
    def from_state(
        cls,
        *,
        state: WorldState,
        max_work_items: int = 20,
        max_votes: int = 20,
        store_max_work_items: int = 200,
        store_max_votes: int = 200,
        history_max_entries: int = 60,
    ) -> "WorldJournal":
        j = cls(
            max_work_items=max_work_items,
            max_votes=max_votes,
            store_max_work_items=store_max_work_items,
            store_max_votes=store_max_votes,
            history_max_entries=history_max_entries,
        )
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

        j.work_item_order = sorted(state.work_items.keys())
        for wid in j.work_item_order:
            j.work_items[wid] = j._work_item_entry(state, wid)

        j.vote_order = sorted(state.votes.keys())
        for vid in j.vote_order:
            j.votes[vid] = j._vote_entry(state, vid)

        j._enforce_caps()
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
            h = self._history_entry(ev)
            if h is not None:
                self.history.append(h)
                changed = True

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
                        self.work_item_order.insert(0, work_id)
                    self._touch(self.work_item_order, work_id)
                    self.work_items[work_id] = self._work_item_entry(state, work_id)
                changed = True
                continue

            if ev.event_type in (
                "work_note_added",
                "work_proposal_submitted",
                "work_item_closed",
                "work_item_archived",
                "work_item_status_changed",
            ):
                work_id = str((ev.payload or {}).get("work_id") or "").strip()
                if work_id and work_id in state.work_items:
                    if work_id not in self.work_items:
                        self.work_item_order.insert(0, work_id)
                    self._touch(self.work_item_order, work_id)
                    self.work_items[work_id] = self._work_item_entry(state, work_id)
                    changed = True
                continue

            if ev.event_type == "vote_opened":
                self.entity_counts["votes"] = int(self.entity_counts.get("votes", 0)) + 1
                vote_id = str((ev.payload or {}).get("vote_id") or "").strip()
                if vote_id and vote_id in state.votes:
                    if vote_id not in self.votes:
                        self.vote_order.insert(0, vote_id)
                    self._touch(self.vote_order, vote_id)
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
                        self.vote_order.insert(0, vote_id)
                    self._touch(self.vote_order, vote_id)
                    self.votes[vote_id] = self._vote_entry(state, vote_id)
                    changed = True
                continue

            if ev.event_type in (
                "position_changed",
                "reputation_modified",
                "reputation_frozen",
                "reputation_unfrozen",
            ):
                target = str((ev.payload or {}).get("target_agent_id") or "").strip()
                if target and target in state.agents:
                    if target not in self.agents:
                        self.agent_ids.append(target)
                        self.agent_ids.sort()
                    self.agents[target] = self._agent_entry(state, target)
                    changed = True
                continue

        if changed:
            self._enforce_caps()
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
            "work_items": [
                self.work_items[wid]
                for wid in self.work_item_order[: self.max_work_items]
                if wid in self.work_items
            ],
            "votes": [
                self.votes[vid]
                for vid in self.vote_order[: self.max_votes]
                if vid in self.votes
            ],
            "history": list(self.history),
        }

    def to_yaml(self) -> str:
        if not self._dirty:
            return self._yaml_cache
        data = self.to_dict()
        if yaml is None:
            self._yaml_cache = str(data)
            self._dirty = False
            return self._yaml_cache

        self._yaml_cache = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        self._dirty = False
        return self._yaml_cache

    @staticmethod
    def _touch(order: list[str], entity_id: str) -> None:
        try:
            order.remove(entity_id)
        except ValueError:
            pass
        order.insert(0, entity_id)

    def _enforce_caps(self) -> None:
        if self.history_max_entries > 0 and len(self.history) > self.history_max_entries:
            self.history = self.history[-self.history_max_entries :]

        if self.store_max_work_items > 0:
            while len(self.work_items) > self.store_max_work_items and self.work_item_order:
                self._evict_one_work_item()

        if self.store_max_votes > 0:
            while len(self.votes) > self.store_max_votes and self.vote_order:
                self._evict_one_vote()

    def _evict_one_vote(self) -> None:
        if not self.vote_order:
            return
        idx: int | None = None
        for i in range(len(self.vote_order) - 1, -1, -1):
            vid = self.vote_order[i]
            status = (self.votes.get(vid) or {}).get("status")
            if status and status != "open":
                idx = i
                break
        if idx is None:
            idx = len(self.vote_order) - 1
        vid = self.vote_order.pop(idx)
        self.votes.pop(vid, None)

    def _evict_one_work_item(self) -> None:
        if not self.work_item_order:
            return
        idx: int | None = None
        for i in range(len(self.work_item_order) - 1, -1, -1):
            wid = self.work_item_order[i]
            status = (self.work_items.get(wid) or {}).get("status")
            if status and status != "open":
                idx = i
                break
        if idx is None:
            idx = len(self.work_item_order) - 1
        wid = self.work_item_order.pop(idx)
        self.work_items.pop(wid, None)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "…"

    def _history_entry(self, ev: Event) -> dict[str, Any] | None:
        p = ev.payload or {}
        t = str(ev.event_type or "")

        if t == "arbiter_approved":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "action_index": p.get("action_index"),
                "reason": self._truncate(str(p.get("reason") or ""), 180),
                "action": self._truncate(str(p.get("action") or ""), 280),
                "ops": list(p.get("ops") or []),
            }
        if t == "arbiter_rejected":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "action_index": p.get("action_index"),
                "reason": self._truncate(str(p.get("reason") or ""), 180),
                "action": self._truncate(str(p.get("action") or ""), 280),
            }
        if t == "arbiter_op_failed":
            err = p.get("error") if isinstance(p, dict) else {}
            return {
                "tick": int(ev.tick),
                "type": t,
                "origin": self._truncate(str(p.get("origin") or ""), 160),
                "op": self._truncate(str(p.get("op") or ""), 200),
                "error_type": (err or {}).get("type") if isinstance(err, dict) else None,
            }

        if t == "message_sent":
            private = bool(p.get("private", True))
            text = str(p.get("text") or "")
            entry = {
                "tick": int(ev.tick),
                "type": t,
                "from_id": ev.actor_id,
                "to_id": str(p.get("to_id") or ""),
                "private": private,
            }
            if private:
                # Приватные сообщения не должны утекать в контекст арбитра.
                entry["text"] = "<redacted>"
                entry["text_len"] = len(text)
            else:
                entry["text"] = self._truncate(text, 240)
            return entry

        if t == "world_event":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "description": self._truncate(str(p.get("description") or ""), 280),
            }

        if t in ("work_note_added", "work_proposal_submitted"):
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "work_id": str(p.get("work_id") or ""),
                "text": self._truncate(str(p.get("text") or ""), 240),
            }

        if t in ("vote_opened", "vote_cast", "vote_closed"):
            entry = {"tick": int(ev.tick), "type": t, "actor_id": ev.actor_id, "vote_id": str(p.get("vote_id") or "")}
            if t == "vote_cast":
                entry["choice"] = str(p.get("choice") or "")
            if t == "vote_opened":
                entry["target_agent_id"] = str(p.get("target_agent_id") or "")
                entry["new_title"] = self._truncate(str(p.get("new_title") or ""), 80)
            if t == "vote_closed":
                entry["result"] = str(p.get("result") or "")
            return entry

        if t in ("vote_target_consented", "vote_target_declined"):
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "vote_id": str(p.get("vote_id") or ""),
                "accept": bool(p.get("accept", False)),
            }

        if t == "position_changed":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "target_agent_id": str(p.get("target_agent_id") or ""),
                "old_title": self._truncate(str(p.get("old_title") or ""), 80),
                "new_title": self._truncate(str(p.get("new_title") or ""), 80),
                "reason": self._truncate(str(p.get("reason") or ""), 180),
            }

        if t == "reputation_modified":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "target_agent_id": str(p.get("target_agent_id") or ""),
                "delta": p.get("delta"),
                "reason": self._truncate(str(p.get("reason") or ""), 180),
            }

        if t in ("reputation_frozen", "reputation_unfrozen"):
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "target_agent_id": str(p.get("target_agent_id") or ""),
                "reason": self._truncate(str(p.get("reason") or ""), 180),
                "until_tick": p.get("until_tick"),
            }

        if t in ("audit_flagged", "audit_case_opened", "audit_case_updated", "audit_escalated", "audit_case_closed"):
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "finding_id": self._truncate(str(p.get("finding_id") or ""), 120),
                "subject_agent_id": str(p.get("subject_agent_id") or ""),
                "target_agent_id": str(p.get("target_agent_id") or ""),
                "violation_type": self._truncate(str(p.get("violation_type") or ""), 120),
                "severity": self._truncate(str(p.get("severity") or ""), 32),
                "summary": self._truncate(str(p.get("summary") or ""), 220),
                "route": self._truncate(str(p.get("route") or ""), 64),
            }

        if t == "entity_created":
            return {
                "tick": int(ev.tick),
                "type": t,
                "actor_id": ev.actor_id,
                "entity_id": str(p.get("entity_id") or ""),
                "kind": str(p.get("kind") or ""),
            }
        return None

    @staticmethod
    def _agent_entry(state: WorldState, agent_id: str) -> dict[str, Any]:
        a = state.agents[agent_id]
        return {
            "id": a.agent_id,
            "name": a.name,
            "internal": a.internal,
            "title": a.title if a.internal else "",
            "reputation": round(float(a.reputation), 3) if a.internal else None,
            "reputation_frozen": bool(a.reputation_frozen) if a.internal else None,
            "reputation_frozen_until_tick": a.reputation_frozen_until_tick if a.internal else None,
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
