"""Детерминированные операции над WorldState (StateOp).

Идея greenfield: StateOp — это атомарное изменение состояния,
которое при успешном применении всегда порождает Event (и пишется в events.jsonl).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .entities import EntityRecord
from .events import Event
from .ids import (
    EntityKind,
    INTERNAL_AUDIENCE,
    PUBLIC_AUDIENCE,
    ensure_kind,
    is_audience_ref,
    parse_typed_id,
)
from .state import Vote, WorkItem, WorldState


class StateOp(Protocol):
    """Протокол атомарной операции."""

    def apply(self, state: WorldState) -> list[Event]:
        """Применить op к миру и вернуть события."""


@dataclass(frozen=True, slots=True)
class CreateEntityOp:
    """Создать сущность в registry (org/channel/work/vote/art)."""

    entity_id: str
    kind: EntityKind
    created_by: str | None
    created_tick: int
    meta: dict[str, Any]

    def apply(self, state: WorldState) -> list[Event]:
        if state.registry.exists(self.entity_id):
            raise ValueError(f"Entity already exists: {self.entity_id!r}")
        state.registry.register(
            EntityRecord(
                entity_id=self.entity_id,
                kind=self.kind,
                created_by=self.created_by,
                created_tick=self.created_tick,
                meta=dict(self.meta),
            )
        )
        return [
            Event(
                tick=state.tick,
                event_type="entity_created",
                actor_id=self.created_by,
                payload={
                    "entity_id": self.entity_id,
                    "kind": self.kind.value,
                    "meta": dict(self.meta),
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class SendMessageOp:
    """Отправить сообщение агенту или в канал."""

    from_id: str
    to_id: str
    text: str
    private: bool = True

    def apply(self, state: WorldState) -> list[Event]:
        if not state.registry.exists(self.from_id):
            raise ValueError(f"Sender not found: {self.from_id!r}")
        if not state.registry.exists(self.to_id):
            raise ValueError(f"Recipient not found: {self.to_id!r}")

        # Private сообщения разрешены только агентам.
        if self.private:
            ensure_kind(self.to_id, EntityKind.AGENT)
            audience = [self.from_id, self.to_id]
        else:
            # Публикация в канал (chan:*) или org:* трактуем как "public-ish".
            parsed = parse_typed_id(self.to_id)
            if parsed.kind not in (EntityKind.CHANNEL, EntityKind.ORG):
                raise ValueError(f"Non-private message must target chan/org, got: {self.to_id!r}")
            audience = [PUBLIC_AUDIENCE]

        return [
            Event(
                tick=state.tick,
                event_type="message_sent",
                actor_id=self.from_id,
                payload={"to_id": self.to_id, "private": self.private, "text": self.text},
                audience=audience,
            )
        ]


@dataclass(frozen=True, slots=True)
class CreateWorkItemOp:
    """Создать work item."""

    created_by: str
    work_id: str
    work_type: str
    title: str
    description: str = ""
    participants: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.work_id, EntityKind.WORK_ITEM)
        if state.registry.exists(self.work_id):
            raise ValueError(f"Work item already exists: {self.work_id!r}")

        participants = list(self.participants or [])
        for pid in participants:
            ensure_kind(pid, EntityKind.AGENT)
            if pid not in state.agents:
                raise ValueError(f"Participant agent not found: {pid!r}")

        state.registry.register(
            EntityRecord(
                entity_id=self.work_id,
                kind=EntityKind.WORK_ITEM,
                created_by=self.created_by,
                created_tick=state.tick,
                meta={"work_type": self.work_type, "title": self.title},
            )
        )
        state.work_items[self.work_id] = WorkItem(
            work_id=self.work_id,
            work_type=self.work_type,
            title=self.title,
            description=self.description,
            participants=participants,
        )
        return [
            Event(
                tick=state.tick,
                event_type="work_item_created",
                actor_id=self.created_by,
                payload={
                    "work_id": self.work_id,
                    "work_type": self.work_type,
                    "title": self.title,
                    "description": self.description,
                    "participants": participants,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class AddWorkNoteOp:
    """Добавить заметку к work item."""

    actor_id: str
    work_id: str
    text: str

    def apply(self, state: WorldState) -> list[Event]:
        if self.work_id not in state.work_items:
            raise ValueError(f"Work item not found: {self.work_id!r}")
        state.work_items[self.work_id].notes.append(self.text)
        return [
            Event(
                tick=state.tick,
                event_type="work_note_added",
                actor_id=self.actor_id,
                payload={"work_id": self.work_id, "text": self.text},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class SubmitWorkProposalOp:
    """Подать предложение по work item."""

    actor_id: str
    work_id: str
    text: str

    def apply(self, state: WorldState) -> list[Event]:
        if self.work_id not in state.work_items:
            raise ValueError(f"Work item not found: {self.work_id!r}")
        state.work_items[self.work_id].proposals.append({"by": self.actor_id, "text": self.text})
        return [
            Event(
                tick=state.tick,
                event_type="work_proposal_submitted",
                actor_id=self.actor_id,
                payload={"work_id": self.work_id, "text": self.text},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class ModifyReputationOp:
    """Изменить репутацию внутреннего агента (репутация не уходит ниже 0)."""

    actor_id: str
    target_agent_id: str
    delta: float
    reason: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.target_agent_id, EntityKind.AGENT)
        agent = state.agents.get(self.target_agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {self.target_agent_id!r}")
        if not agent.internal:
            # Внешние сущности не имеют репутации в системе.
            return [
                Event(
                    tick=state.tick,
                    event_type="reputation_ignored_external",
                    actor_id=self.actor_id,
                    payload={"target_agent_id": self.target_agent_id, "delta": self.delta, "reason": self.reason},
                    audience=[INTERNAL_AUDIENCE],
                )
            ]
        agent.reputation = max(0.0, agent.reputation + self.delta)
        return [
            Event(
                tick=state.tick,
                event_type="reputation_modified",
                actor_id=self.actor_id,
                payload={"target_agent_id": self.target_agent_id, "delta": self.delta, "reason": self.reason},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class OpenVoteOp:
    """Открыть DAO-голосование."""

    vote_id: str
    created_by: str
    created_tick: int
    closes_tick: int
    target_agent_id: str
    new_title: str
    reason: str
    voters: list[str]

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.vote_id, EntityKind.VOTE)
        if state.registry.exists(self.vote_id):
            raise ValueError(f"Vote already exists: {self.vote_id!r}")
        if self.target_agent_id not in state.agents:
            raise ValueError(f"Target agent not found: {self.target_agent_id!r}")
        for v in self.voters:
            ensure_kind(v, EntityKind.AGENT)
            if v not in state.agents:
                raise ValueError(f"Voter agent not found: {v!r}")

        state.registry.register(
            EntityRecord(
                entity_id=self.vote_id,
                kind=EntityKind.VOTE,
                created_by=self.created_by,
                created_tick=self.created_tick,
                meta={"vote_type": "position_change"},
            )
        )
        state.votes[self.vote_id] = Vote(
            vote_id=self.vote_id,
            vote_type="position_change",
            created_by=self.created_by,
            created_tick=self.created_tick,
            closes_tick=self.closes_tick,
            target_agent_id=self.target_agent_id,
            new_title=self.new_title,
            reason=self.reason,
            voters=list(self.voters),
        )
        return [
            Event(
                tick=state.tick,
                event_type="vote_opened",
                actor_id=self.created_by,
                payload={
                    "vote_id": self.vote_id,
                    "vote_type": "position_change",
                    "target_agent_id": self.target_agent_id,
                    "new_title": self.new_title,
                    "reason": self.reason,
                    "voters": list(self.voters),
                    "closes_tick": self.closes_tick,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class CastVoteOp:
    """Отдать голос в DAO."""

    actor_id: str
    vote_id: str
    choice: str

    def apply(self, state: WorldState) -> list[Event]:
        vote = state.votes.get(self.vote_id)
        if vote is None:
            raise ValueError(f"Vote not found: {self.vote_id!r}")
        if vote.status != "open":
            raise ValueError(f"Vote is not open: {self.vote_id!r}")
        if self.actor_id not in vote.voters:
            raise ValueError(f"Actor is not eligible voter: {self.actor_id!r}")
        vote.votes[self.actor_id] = self.choice
        return [
            Event(
                tick=state.tick,
                event_type="vote_cast",
                actor_id=self.actor_id,
                payload={"vote_id": self.vote_id, "choice": self.choice},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class SetVoteConsentOp:
    """Зафиксировать согласие/отказ цели голосования."""

    actor_id: str
    vote_id: str
    accept: bool

    def apply(self, state: WorldState) -> list[Event]:
        vote = state.votes.get(self.vote_id)
        if vote is None:
            raise ValueError(f"Vote not found: {self.vote_id!r}")
        if vote.status != "open":
            raise ValueError(f"Vote is not open: {self.vote_id!r}")
        if self.actor_id != vote.target_agent_id:
            raise ValueError("Only target agent can respond to nomination")
        vote.target_consented = bool(self.accept)
        return [
            Event(
                tick=state.tick,
                event_type="vote_target_consented" if self.accept else "vote_target_declined",
                actor_id=self.actor_id,
                payload={"vote_id": self.vote_id, "accept": self.accept},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class CloseVoteOp:
    """Закрыть голосование (результат считается детерминированно)."""

    vote_id: str
    result: str

    def apply(self, state: WorldState) -> list[Event]:
        vote = state.votes.get(self.vote_id)
        if vote is None:
            raise ValueError(f"Vote not found: {self.vote_id!r}")
        if vote.status != "open":
            raise ValueError(f"Vote already closed: {self.vote_id!r}")
        vote.status = "closed"
        vote.result = self.result
        return [
            Event(
                tick=state.tick,
                event_type="vote_closed",
                actor_id=None,
                payload={"vote_id": self.vote_id, "result": self.result},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class ChangePositionOp:
    """Изменить должность внутреннего агента."""

    actor_id: str | None
    target_agent_id: str
    new_title: str
    reason: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        agent = state.agents.get(self.target_agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {self.target_agent_id!r}")
        if not agent.internal:
            raise ValueError("Cannot change position of external agent")
        old = agent.title
        agent.title = self.new_title
        return [
            Event(
                tick=state.tick,
                event_type="position_changed",
                actor_id=self.actor_id,
                payload={
                    "target_agent_id": self.target_agent_id,
                    "old_title": old,
                    "new_title": self.new_title,
                    "reason": self.reason,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]
