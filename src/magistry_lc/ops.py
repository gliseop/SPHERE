"""Детерминированные операции над WorldState (StateOp).

Идея greenfield: StateOp — это атомарное изменение состояния,
которое при успешном применении всегда порождает Event (и пишется в events.jsonl).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from .persona import PersonaArtifact
from .state import AgentState, AuditCase, Vote, WorkItem, WorldState
from .utils import normalize_agent_display_name


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
class CreateAgentOp:
    """Создать нового агента в registry и state."""

    entity_id: str
    name: str
    internal: bool
    persona_hint: str
    capabilities: list[str]
    created_by: str | None
    created_tick: int

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.entity_id, EntityKind.AGENT)
        if state.registry.exists(self.entity_id) or self.entity_id in state.agents:
            raise ValueError(f"Agent already exists: {self.entity_id!r}")
        display_name = normalize_agent_display_name(self.name, fallback=self.entity_id)
        if not display_name:
            raise ValueError("Agent name must be human-readable")

        meta = {
            "name": display_name,
            "internal": bool(self.internal),
            "capabilities": list(self.capabilities),
        }
        state.registry.register(
            EntityRecord(
                entity_id=self.entity_id,
                kind=EntityKind.AGENT,
                created_by=self.created_by,
                created_tick=self.created_tick,
                meta=meta,
            )
        )
        state.agents[self.entity_id] = AgentState(
            agent_id=self.entity_id,
            name=display_name,
            internal=bool(self.internal),
            persona=PersonaArtifact(summary=(self.persona_hint or "").strip()),
            capabilities=list(self.capabilities),
            wants_promotion=False,
        )
        return [
            Event(
                tick=state.tick,
                event_type="entity_created",
                actor_id=self.created_by,
                payload={"entity_id": self.entity_id, "kind": EntityKind.AGENT.value, "meta": meta},
                audience=[INTERNAL_AUDIENCE],
            ),
            Event(
                tick=state.tick,
                event_type="reputation_snapshot",
                actor_id=None,
                payload={
                    "target_agent_id": self.entity_id,
                    "score": state.agents[self.entity_id].reputation,
                    "internal": state.agents[self.entity_id].internal,
                    "frozen": state.agents[self.entity_id].reputation_frozen,
                    "frozen_until_tick": state.agents[self.entity_id].reputation_frozen_until_tick,
                    "title": state.agents[self.entity_id].title,
                },
                audience=[INTERNAL_AUDIENCE],
            ),
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

    created_by: str | None
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

    actor_id: str | None
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
        if agent.reputation_frozen and self.delta > 0:
            return [
                Event(
                    tick=state.tick,
                    event_type="reputation_gain_blocked",
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
class SetReputationFreezeOp:
    """Заморозить или разморозить репутацию внутреннего агента."""

    actor_id: str | None
    target_agent_id: str
    frozen: bool
    reason: str = ""
    until_tick: int | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.target_agent_id, EntityKind.AGENT)
        agent = state.agents.get(self.target_agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {self.target_agent_id!r}")
        if not agent.internal:
            return [
                Event(
                    tick=state.tick,
                    event_type="reputation_ignored_external",
                    actor_id=self.actor_id,
                    payload={
                        "target_agent_id": self.target_agent_id,
                        "frozen": self.frozen,
                        "reason": self.reason,
                        "until_tick": self.until_tick,
                    },
                    audience=[INTERNAL_AUDIENCE],
                )
            ]

        if self.frozen:
            agent.reputation_frozen = True
            agent.reputation_frozen_until_tick = self.until_tick
            return [
                Event(
                    tick=state.tick,
                    event_type="reputation_frozen",
                    actor_id=self.actor_id,
                    payload={
                        "target_agent_id": self.target_agent_id,
                        "reason": self.reason,
                        "until_tick": self.until_tick,
                    },
                    audience=[INTERNAL_AUDIENCE],
                )
            ]

        agent.reputation_frozen = False
        agent.reputation_frozen_until_tick = None
        return [
            Event(
                tick=state.tick,
                event_type="reputation_unfrozen",
                actor_id=self.actor_id,
                payload={"target_agent_id": self.target_agent_id, "reason": self.reason},
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class OpenAuditCaseOp:
    """Открыть audit-case в состоянии мира."""

    actor_id: str | None
    case_id: str
    finding_id: str
    subject_agent_id: str
    risk_family: str
    violation_type: str
    summary: str
    recommended_action: str
    confidence: float
    target_agent_id: str | None = None
    beneficiary: str | None = None
    related_agent_ids: list[str] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    response_requested_tick: int | None = None
    response_due_tick: int | None = None
    monitoring: bool = False
    review_vote_id: str | None = None

    def apply(self, state: WorldState) -> list[Event]:
        if self.case_id in state.audit_cases:
            raise ValueError(f"Audit case already exists: {self.case_id!r}")
        state.audit_cases[self.case_id] = AuditCase(
            case_id=self.case_id,
            finding_id=self.finding_id,
            created_tick=state.tick,
            subject_agent_id=self.subject_agent_id,
            target_agent_id=self.target_agent_id,
            risk_family=self.risk_family,
            violation_type=self.violation_type,
            summary=self.summary,
            recommended_action=self.recommended_action,
            confidence=float(self.confidence),
            beneficiary=self.beneficiary,
            related_agent_ids=list(self.related_agent_ids or []),
            evidence_refs=list(self.evidence_refs or []),
            finding_ids=[self.finding_id],
            episode_count=1,
            updated_tick=state.tick,
            last_finding_tick=state.tick,
            response_requested_tick=self.response_requested_tick,
            response_due_tick=self.response_due_tick,
            monitoring=bool(self.monitoring),
            review_vote_id=self.review_vote_id,
        )
        return [
            Event(
                tick=state.tick,
                event_type="audit_case_opened",
                actor_id=self.actor_id,
                payload={
                    "case_id": self.case_id,
                    "finding_id": self.finding_id,
                    "subject_agent_id": self.subject_agent_id,
                    "target_agent_id": self.target_agent_id,
                    "counterparty_agent_id": self.target_agent_id,
                    "related_target_agent_id": self.target_agent_id,
                    "risk_family": self.risk_family,
                    "violation_type": self.violation_type,
                    "summary": self.summary,
                    "recommended_action": self.recommended_action,
                    "confidence": self.confidence,
                    "beneficiary": self.beneficiary,
                    "related_agent_ids": list(self.related_agent_ids or []),
                    "evidence_refs": list(self.evidence_refs or []),
                    "episode_count": 1,
                    "response_requested_tick": self.response_requested_tick,
                    "response_due_tick": self.response_due_tick,
                    "monitoring": bool(self.monitoring),
                    "review_vote_id": self.review_vote_id,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateAuditCaseOp:
    """Обновить существующий audit-case новым эпизодом или policy-состоянием."""

    actor_id: str | None
    case_id: str
    finding_id: str | None = None
    summary: str | None = None
    recommended_action: str | None = None
    confidence: float | None = None
    target_agent_id: str | None = None
    beneficiary: str | None = None
    related_agent_ids: list[str] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    response_requested_tick: int | None = None
    response_due_tick: int | None = None
    monitoring: bool | None = None
    review_vote_id: str | None = None
    status: str | None = None
    bump_episode: bool = True

    def apply(self, state: WorldState) -> list[Event]:
        case = state.audit_cases.get(self.case_id)
        if case is None:
            raise ValueError(f"Audit case not found: {self.case_id!r}")
        if case.status == "closed":
            raise ValueError(f"Audit case already closed: {self.case_id!r}")

        if self.finding_id and self.finding_id not in case.finding_ids:
            case.finding_ids.append(self.finding_id)
        if self.bump_episode:
            case.episode_count += 1
            case.last_finding_tick = state.tick
        case.updated_tick = state.tick
        if self.summary:
            case.summary = self.summary
        if self.recommended_action:
            case.recommended_action = self.recommended_action
        if self.confidence is not None:
            case.confidence = max(float(case.confidence), float(self.confidence))
        if self.target_agent_id is not None:
            case.target_agent_id = self.target_agent_id
        if self.beneficiary is not None:
            case.beneficiary = self.beneficiary
        if self.related_agent_ids:
            merged_related = list(case.related_agent_ids)
            for agent_id in self.related_agent_ids:
                if agent_id not in merged_related:
                    merged_related.append(agent_id)
            case.related_agent_ids = merged_related
        if self.evidence_refs:
            merged_evidence = list(case.evidence_refs)
            for item in self.evidence_refs:
                if item not in merged_evidence:
                    merged_evidence.append(item)
            case.evidence_refs = merged_evidence
        if self.response_requested_tick is not None:
            case.response_requested_tick = self.response_requested_tick
        if self.response_due_tick is not None:
            case.response_due_tick = self.response_due_tick
        if self.monitoring is not None:
            case.monitoring = bool(self.monitoring)
        if self.review_vote_id is not None:
            case.review_vote_id = self.review_vote_id
        if self.status is not None:
            case.status = self.status

        return [
            Event(
                tick=state.tick,
                event_type="audit_case_updated",
                actor_id=self.actor_id,
                payload={
                    "case_id": self.case_id,
                    "finding_id": self.finding_id,
                    "subject_agent_id": case.subject_agent_id,
                    "target_agent_id": case.target_agent_id,
                    "counterparty_agent_id": case.target_agent_id,
                    "violation_type": case.violation_type,
                    "summary": case.summary,
                    "recommended_action": case.recommended_action,
                    "confidence": case.confidence,
                    "beneficiary": case.beneficiary,
                    "related_agent_ids": list(case.related_agent_ids),
                    "evidence_refs": list(case.evidence_refs),
                    "episode_count": case.episode_count,
                    "response_requested_tick": case.response_requested_tick,
                    "response_due_tick": case.response_due_tick,
                    "monitoring": case.monitoring,
                    "review_vote_id": case.review_vote_id,
                    "status": case.status,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class CloseAuditCaseOp:
    """Закрыть audit-case."""

    actor_id: str | None
    case_id: str
    result: str
    reason: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        case = state.audit_cases.get(self.case_id)
        if case is None:
            raise ValueError(f"Audit case not found: {self.case_id!r}")
        if case.status == "closed":
            raise ValueError(f"Audit case already closed: {self.case_id!r}")
        case.status = "closed"
        case.result = self.result
        case.result_reason = self.reason
        return [
            Event(
                tick=state.tick,
                event_type="audit_case_closed",
                actor_id=self.actor_id,
                payload={"case_id": self.case_id, "result": self.result, "reason": self.reason},
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
    vote_type: str = "position_change"
    summary: str = ""
    metadata: dict[str, Any] | None = None

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
                meta={"vote_type": self.vote_type},
            )
        )
        state.votes[self.vote_id] = Vote(
            vote_id=self.vote_id,
            vote_type=self.vote_type,
            created_by=self.created_by,
            created_tick=self.created_tick,
            closes_tick=self.closes_tick,
            target_agent_id=self.target_agent_id,
            new_title=self.new_title,
            reason=self.reason,
            voters=list(self.voters),
            metadata=dict(self.metadata or {}),
        )
        payload = {
            "vote_id": self.vote_id,
            "vote_type": self.vote_type,
            "target_agent_id": self.target_agent_id,
            "new_title": self.new_title,
            "reason": self.reason,
            "summary": self.summary,
            "voters": list(self.voters),
            "closes_tick": self.closes_tick,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        events = [
            Event(
                tick=state.tick,
                event_type="vote_opened",
                actor_id=self.created_by,
                payload=payload,
                audience=[INTERNAL_AUDIENCE],
            )
        ]
        if self.vote_type == "audit_review":
            events.append(
                Event(
                    tick=state.tick,
                    event_type="review_case_opened",
                    actor_id=self.created_by,
                    payload=payload,
                    audience=[INTERNAL_AUDIENCE],
                )
            )
        return events


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
    reason: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        vote = state.votes.get(self.vote_id)
        if vote is None:
            raise ValueError(f"Vote not found: {self.vote_id!r}")
        if vote.status != "open":
            raise ValueError(f"Vote already closed: {self.vote_id!r}")
        vote.status = "closed"
        vote.result = self.result
        vote.result_reason = self.reason
        payload = {"vote_id": self.vote_id, "result": self.result, "reason": self.reason, "vote_type": vote.vote_type}
        if vote.metadata:
            payload["metadata"] = dict(vote.metadata)
        events = [
            Event(
                tick=state.tick,
                event_type="vote_closed",
                actor_id=None,
                payload=payload,
                audience=[INTERNAL_AUDIENCE],
            )
        ]
        if vote.vote_type == "audit_review":
            events.append(
                Event(
                    tick=state.tick,
                    event_type="review_case_closed",
                    actor_id=None,
                    payload=payload,
                    audience=[INTERNAL_AUDIENCE],
                )
            )
        return events


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
