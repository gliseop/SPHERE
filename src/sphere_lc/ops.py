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
from .state import (
    AgentState,
    ArtifactState,
    AuditCase,
    InformationClimateState,
    InformalLinkState,
    InstitutionRegimeState,
    PendingInteractionState,
    ResourcePoolState,
    Vote,
    WorkItem,
    WorldState,
    ZoneState,
    informal_link_key,
)
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
    org_id: str | None = None
    zone_id: str | None = None
    spawn_source: str = ""
    blueprint_id: str | None = None
    population_role: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.entity_id, EntityKind.AGENT)
        if state.registry.exists(self.entity_id) or self.entity_id in state.agents:
            raise ValueError(f"Agent already exists: {self.entity_id!r}")
        display_name = normalize_agent_display_name(self.name, fallback=self.entity_id)
        if not display_name:
            raise ValueError("Agent name must be human-readable")
        if self.org_id is not None:
            ensure_kind(self.org_id, EntityKind.ORG)
            if not state.registry.exists(self.org_id):
                raise ValueError(f"Unknown org_id: {self.org_id!r}")
        if self.zone_id is not None:
            ensure_kind(self.zone_id, EntityKind.ZONE)
            if not state.registry.exists(self.zone_id):
                raise ValueError(f"Unknown zone_id: {self.zone_id!r}")

        meta = {
            "name": display_name,
            "internal": bool(self.internal),
            "capabilities": list(self.capabilities),
            "org_id": self.org_id,
            "zone_id": self.zone_id,
            "spawn_source": self.spawn_source,
            "blueprint_id": self.blueprint_id,
            "population_role": self.population_role,
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
            org_id=self.org_id,
            zone_id=self.zone_id,
            spawn_source=self.spawn_source,
            blueprint_id=self.blueprint_id,
            population_role=self.population_role,
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
            sender = state.agents.get(self.from_id)
            recipient = state.agents.get(self.to_id)
            if sender is None or recipient is None:
                raise ValueError("Private message requires agent sender and recipient")
            if (
                sender.zone_id is not None
                and recipient.zone_id is not None
                and sender.zone_id != recipient.zone_id
            ):
                raise ValueError(
                    f"private_contact_requires_shared_zone:{self.from_id}:{self.to_id}:{sender.zone_id}!={recipient.zone_id}"
                )
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
class RecordNarrativeActionOp:
    """Зафиксировать физическое или неформальное действие агента в мире.

    Используется когда proposal агента описывает наблюдаемое действие,
    не сводимое целиком к формальным операциям: перемещение, передача
    документа из рук в руки, осмотр, намёк в частной беседе, ожидание
    в приёмной и т.п. Может сочетаться с формальными ops в одном ходе.
    """

    actor_id: str
    description: str
    action_kind: str = "general"
    zone_id: str | None = None
    witnesses: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        if self.zone_id is not None:
            ensure_kind(self.zone_id, EntityKind.ZONE)
        valid_witnesses: list[str] = []
        for w in (self.witnesses or []):
            if w in state.agents and w != self.actor_id:
                valid_witnesses.append(w)
        if valid_witnesses:
            audience = list({self.actor_id} | set(valid_witnesses))
        else:
            audience = [INTERNAL_AUDIENCE]
        return [
            Event(
                tick=state.tick,
                event_type="narrative_action",
                actor_id=self.actor_id,
                payload={
                    "description": self.description,
                    "action_kind": self.action_kind or "general",
                    "zone_id": self.zone_id,
                    "witnesses": valid_witnesses,
                },
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
class CreateArtifactOp:
    """Создать документ/артефакт мира."""

    created_by: str | None
    artifact_id: str
    artifact_type: str
    title: str
    summary: str = ""
    owner_org_id: str | None = None
    zone_id: str | None = None
    related_work_id: str | None = None
    visibility: str = "internal"
    status: str = "active"
    tags: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.artifact_id, EntityKind.ARTIFACT)
        if state.registry.exists(self.artifact_id) or self.artifact_id in state.artifacts:
            raise ValueError(f"Artifact already exists: {self.artifact_id!r}")
        if self.owner_org_id is not None:
            ensure_kind(self.owner_org_id, EntityKind.ORG)
            if not state.registry.exists(self.owner_org_id):
                raise ValueError(f"Unknown owner_org_id: {self.owner_org_id!r}")
        if self.zone_id is not None:
            ensure_kind(self.zone_id, EntityKind.ZONE)
            if not state.registry.exists(self.zone_id):
                raise ValueError(f"Unknown zone_id: {self.zone_id!r}")
        if self.related_work_id is not None:
            ensure_kind(self.related_work_id, EntityKind.WORK_ITEM)
            if self.related_work_id not in state.work_items:
                raise ValueError(f"Unknown related_work_id: {self.related_work_id!r}")

        meta = {
            "artifact_type": self.artifact_type,
            "title": self.title,
            "owner_org_id": self.owner_org_id,
            "zone_id": self.zone_id,
            "related_work_id": self.related_work_id,
            "visibility": self.visibility,
            "status": self.status,
            "tags": list(self.tags or []),
        }
        state.registry.register(
            EntityRecord(
                entity_id=self.artifact_id,
                kind=EntityKind.ARTIFACT,
                created_by=self.created_by,
                created_tick=state.tick,
                meta=meta,
            )
        )
        state.artifacts[self.artifact_id] = ArtifactState(
            artifact_id=self.artifact_id,
            artifact_type=self.artifact_type,
            title=self.title,
            summary=self.summary,
            owner_org_id=self.owner_org_id,
            zone_id=self.zone_id,
            related_work_id=self.related_work_id,
            visibility=self.visibility,
            status=self.status,
            tags=list(self.tags or []),
        )
        return [
            Event(
                tick=state.tick,
                event_type="artifact_created",
                actor_id=self.created_by,
                payload={
                    "artifact_id": self.artifact_id,
                    "artifact_type": self.artifact_type,
                    "title": self.title,
                    "summary": self.summary,
                    "owner_org_id": self.owner_org_id,
                    "zone_id": self.zone_id,
                    "related_work_id": self.related_work_id,
                    "visibility": self.visibility,
                    "status": self.status,
                    "tags": list(self.tags or []),
                },
                audience=[PUBLIC_AUDIENCE] if self.visibility == "public" else [INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateArtifactOp:
    """Обновить документ/артефакт мира."""

    actor_id: str | None
    artifact_id: str
    title: str | None = None
    summary: str | None = None
    status: str | None = None
    visibility: str | None = None
    tags: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.artifact_id, EntityKind.ARTIFACT)
        artifact = state.artifacts.get(self.artifact_id)
        if artifact is None:
            raise ValueError(f"Unknown artifact: {self.artifact_id!r}")
        artifact.title = self.title or artifact.title
        artifact.summary = self.summary or artifact.summary
        artifact.status = self.status or artifact.status
        artifact.visibility = self.visibility or artifact.visibility
        if self.tags is not None:
            artifact.tags = list(self.tags)
        record = state.registry.get(self.artifact_id)
        if record is not None:
            record.meta.update(
                {
                    "title": artifact.title,
                    "visibility": artifact.visibility,
                    "status": artifact.status,
                    "tags": list(artifact.tags),
                }
            )
        return [
            Event(
                tick=state.tick,
                event_type="artifact_updated",
                actor_id=self.actor_id,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "visibility": artifact.visibility,
                    "status": artifact.status,
                    "tags": list(artifact.tags),
                },
                audience=[PUBLIC_AUDIENCE] if artifact.visibility == "public" else [INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateInstitutionRegimeOp:
    """Обновить режим организации в environment-layer."""

    org_id: str
    operating_mode: str | None = None
    transparency_mode: str | None = None
    access_mode: str | None = None
    security_mode: str | None = None
    capture_risk: str | None = None
    linked_zone_ids: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.org_id, EntityKind.ORG)
        if not state.registry.exists(self.org_id):
            raise ValueError(f"Unknown organization: {self.org_id!r}")
        if self.linked_zone_ids is not None:
            for zone_id in self.linked_zone_ids:
                ensure_kind(zone_id, EntityKind.ZONE)
                if zone_id not in state.environment.zones:
                    raise ValueError(f"Unknown linked zone: {zone_id!r}")
        current = state.environment.institutions.get(self.org_id) or InstitutionRegimeState(org_id=self.org_id)
        linked_zone_ids = list(current.linked_zone_ids)
        if self.linked_zone_ids is not None:
            linked_zone_ids = list(self.linked_zone_ids)
        updated = InstitutionRegimeState(
            org_id=self.org_id,
            operating_mode=self.operating_mode or current.operating_mode,
            transparency_mode=self.transparency_mode or current.transparency_mode,
            access_mode=self.access_mode or current.access_mode,
            security_mode=self.security_mode or current.security_mode,
            capture_risk=self.capture_risk or current.capture_risk,
            linked_zone_ids=linked_zone_ids,
        )
        state.environment.institutions[self.org_id] = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_institution_updated",
                actor_id=None,
                payload={
                    "org_id": updated.org_id,
                    "operating_mode": updated.operating_mode,
                    "transparency_mode": updated.transparency_mode,
                    "access_mode": updated.access_mode,
                    "security_mode": updated.security_mode,
                    "capture_risk": updated.capture_risk,
                    "linked_zone_ids": list(updated.linked_zone_ids),
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateZoneStateOp:
    """Обновить режим зоны."""

    zone_id: str
    access_mode: str | None = None
    transparency_mode: str | None = None
    security_level: str | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.zone_id, EntityKind.ZONE)
        if not state.registry.exists(self.zone_id) or self.zone_id not in state.environment.zones:
            raise ValueError(f"Unknown zone: {self.zone_id!r}")
        current = state.environment.zones[self.zone_id]
        updated = ZoneState(
            zone_id=current.zone_id,
            title=current.title,
            zone_type=current.zone_type,
            primary_org_id=current.primary_org_id,
            access_mode=self.access_mode or current.access_mode,
            transparency_mode=self.transparency_mode or current.transparency_mode,
            security_level=self.security_level or current.security_level,
        )
        state.environment.zones[self.zone_id] = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_zone_updated",
                actor_id=None,
                payload={
                    "zone_id": updated.zone_id,
                    "access_mode": updated.access_mode,
                    "transparency_mode": updated.transparency_mode,
                    "security_level": updated.security_level,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateResourcePoolOp:
    """Обновить ресурсный контур среды."""

    resource_id: str
    quantity: float | None = None
    status: str | None = None
    pressure: str | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.resource_id, EntityKind.RESOURCE)
        if not state.registry.exists(self.resource_id) or self.resource_id not in state.environment.resource_pools:
            raise ValueError(f"Unknown resource pool: {self.resource_id!r}")
        current = state.environment.resource_pools[self.resource_id]
        quantity = current.quantity if self.quantity is None else float(self.quantity)
        if quantity < 0.0:
            raise ValueError("Resource pool quantity must be >= 0")
        updated = ResourcePoolState(
            resource_id=current.resource_id,
            title=current.title,
            owner_org_id=current.owner_org_id,
            quantity=quantity,
            unit=current.unit,
            status=self.status or current.status,
            pressure=self.pressure or current.pressure,
        )
        state.environment.resource_pools[self.resource_id] = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_resource_updated",
                actor_id=None,
                payload={
                    "resource_id": updated.resource_id,
                    "quantity": updated.quantity,
                    "status": updated.status,
                    "pressure": updated.pressure,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpdateInformationClimateOp:
    """Обновить глобальный информационный климат."""

    public_mood: str | None = None
    oversight_attention: str | None = None
    media_pressure: str | None = None
    narrative_temperature: str | None = None
    active_signals: list[str] | None = None

    def apply(self, state: WorldState) -> list[Event]:
        current = state.environment.information_climate
        updated = InformationClimateState(
            public_mood=self.public_mood or current.public_mood,
            oversight_attention=self.oversight_attention or current.oversight_attention,
            media_pressure=self.media_pressure or current.media_pressure,
            narrative_temperature=self.narrative_temperature or current.narrative_temperature,
            active_signals=list(self.active_signals) if self.active_signals is not None else list(current.active_signals),
        )
        state.environment.information_climate = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_information_climate_updated",
                actor_id=None,
                payload={
                    "public_mood": updated.public_mood,
                    "oversight_attention": updated.oversight_attention,
                    "media_pressure": updated.media_pressure,
                    "narrative_temperature": updated.narrative_temperature,
                    "active_signals": list(updated.active_signals),
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class AddInformationSignalOp:
    """Добавить новый signal в information_climate без потери существующих."""

    actor_id: str | None
    signal: str

    def apply(self, state: WorldState) -> list[Event]:
        signal = (self.signal or "").strip()
        if not signal:
            return []
        current = state.environment.information_climate
        if signal in current.active_signals:
            return []
        updated = InformationClimateState(
            public_mood=current.public_mood,
            oversight_attention=current.oversight_attention,
            media_pressure=current.media_pressure,
            narrative_temperature=current.narrative_temperature,
            active_signals=list(current.active_signals) + [signal],
        )
        state.environment.information_climate = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_information_climate_updated",
                actor_id=self.actor_id,
                payload={
                    "public_mood": updated.public_mood,
                    "oversight_attention": updated.oversight_attention,
                    "media_pressure": updated.media_pressure,
                    "narrative_temperature": updated.narrative_temperature,
                    "active_signals": list(updated.active_signals),
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpsertInformalLinkOp:
    """Создать или обновить неформальную связь между агентами."""

    actor_id: str | None
    agent_a_id: str
    agent_b_id: str
    link_type: str
    strength: float | None = None
    strength_delta: float | None = None
    visibility: str | None = None
    pressure: str | None = None
    source: str | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.agent_a_id, EntityKind.AGENT)
        ensure_kind(self.agent_b_id, EntityKind.AGENT)
        if self.agent_a_id not in state.agents or self.agent_b_id not in state.agents:
            raise ValueError("Informal link agents must exist in world state")
        link_type = (self.link_type or "").strip()
        if not link_type:
            raise ValueError("Informal link requires link_type")
        link_id = informal_link_key(self.agent_a_id, self.agent_b_id, link_type)
        left, right = sorted([self.agent_a_id, self.agent_b_id])
        current = state.environment.informal_links.get(link_id)
        current_strength = float(current.strength) if current is not None else 0.0
        if self.strength is not None:
            next_strength = float(self.strength)
        else:
            next_strength = current_strength + float(self.strength_delta or 0.0)
        next_strength = max(0.0, min(1.0, next_strength))
        updated = InformalLinkState(
            link_id=link_id,
            agent_a_id=left,
            agent_b_id=right,
            link_type=link_type,
            strength=next_strength,
            visibility=self.visibility or (current.visibility if current is not None else "latent"),
            pressure=self.pressure or (current.pressure if current is not None else ""),
            source=self.source or (current.source if current is not None else "interaction"),
            last_updated_tick=state.tick,
        )
        state.environment.informal_links[link_id] = updated
        return [
            Event(
                tick=state.tick,
                event_type="environment_informal_link_updated",
                actor_id=self.actor_id,
                payload={
                    "link_id": updated.link_id,
                    "agent_a_id": updated.agent_a_id,
                    "agent_b_id": updated.agent_b_id,
                    "link_type": updated.link_type,
                    "strength": updated.strength,
                    "visibility": updated.visibility,
                    "pressure": updated.pressure,
                    "source": updated.source,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class UpsertPendingInteractionOp:
    """Создать или обновить ожидающее локальное взаимодействие."""

    actor_id: str | None
    interaction_id: str
    target_agent_id: str
    source_agent_id: str | None = None
    category: str = "follow_up"
    summary: str = ""
    earliest_tick: int | None = None
    due_tick: int | None = None
    priority: str = "normal"
    trigger_event_type: str = ""
    related_work_id: str | None = None
    artifact_id: str | None = None
    org_id: str | None = None
    zone_id: str | None = None

    def apply(self, state: WorldState) -> list[Event]:
        ensure_kind(self.target_agent_id, EntityKind.AGENT)
        if self.target_agent_id not in state.agents:
            raise ValueError(f"Pending interaction target not found: {self.target_agent_id!r}")
        if self.source_agent_id is not None:
            ensure_kind(self.source_agent_id, EntityKind.AGENT)
            if self.source_agent_id not in state.agents:
                raise ValueError(f"Pending interaction source not found: {self.source_agent_id!r}")
        if self.related_work_id is not None:
            ensure_kind(self.related_work_id, EntityKind.WORK_ITEM)
            if self.related_work_id not in state.work_items:
                raise ValueError(f"Pending interaction work item not found: {self.related_work_id!r}")
        if self.artifact_id is not None:
            ensure_kind(self.artifact_id, EntityKind.ARTIFACT)
            if self.artifact_id not in state.artifacts:
                raise ValueError(f"Pending interaction artifact not found: {self.artifact_id!r}")
        if self.org_id is not None:
            ensure_kind(self.org_id, EntityKind.ORG)
            if not state.registry.exists(self.org_id):
                raise ValueError(f"Pending interaction org not found: {self.org_id!r}")
        if self.zone_id is not None:
            ensure_kind(self.zone_id, EntityKind.ZONE)
            if not state.registry.exists(self.zone_id):
                raise ValueError(f"Pending interaction zone not found: {self.zone_id!r}")

        category = (self.category or "").strip() or "follow_up"
        current = state.pending_interactions.get(self.interaction_id)
        created_tick = state.tick if current is None or current.status != "open" else current.created_tick
        updated = PendingInteractionState(
            interaction_id=self.interaction_id,
            target_agent_id=self.target_agent_id,
            source_agent_id=self.source_agent_id,
            category=category,
            summary=(self.summary or "").strip(),
            created_tick=created_tick,
            earliest_tick=int(self.earliest_tick if self.earliest_tick is not None else state.tick),
            due_tick=self.due_tick,
            priority=((self.priority or "").strip() or (current.priority if current is not None else "normal")),
            status="open",
            resolution_reason="",
            trigger_event_type=((self.trigger_event_type or "").strip() or (current.trigger_event_type if current is not None else "")),
            related_work_id=self.related_work_id,
            artifact_id=self.artifact_id,
            org_id=self.org_id,
            zone_id=self.zone_id,
            last_notified_tick=None,
        )
        state.pending_interactions[self.interaction_id] = updated
        event_type = "pending_interaction_created" if current is None or current.status != "open" else "pending_interaction_updated"
        return [
            Event(
                tick=state.tick,
                event_type=event_type,
                actor_id=self.actor_id,
                payload={
                    "interaction_id": updated.interaction_id,
                    "target_agent_id": updated.target_agent_id,
                    "source_agent_id": updated.source_agent_id,
                    "category": updated.category,
                    "summary": updated.summary,
                    "created_tick": updated.created_tick,
                    "earliest_tick": updated.earliest_tick,
                    "due_tick": updated.due_tick,
                    "priority": updated.priority,
                    "trigger_event_type": updated.trigger_event_type,
                    "related_work_id": updated.related_work_id,
                    "artifact_id": updated.artifact_id,
                    "org_id": updated.org_id,
                    "zone_id": updated.zone_id,
                },
                audience=[INTERNAL_AUDIENCE],
            )
        ]


@dataclass(frozen=True, slots=True)
class ResolvePendingInteractionOp:
    """Закрыть ожидающее взаимодействие как выполненное или просроченное."""

    actor_id: str | None
    interaction_id: str
    status: str
    reason: str = ""

    def apply(self, state: WorldState) -> list[Event]:
        interaction = state.pending_interactions.get(self.interaction_id)
        if interaction is None:
            raise ValueError(f"Pending interaction not found: {self.interaction_id!r}")
        next_status = (self.status or "").strip()
        if next_status not in {"completed", "expired"}:
            raise ValueError(f"Unsupported pending interaction status: {self.status!r}")
        interaction.status = next_status
        interaction.resolution_reason = (self.reason or "").strip()
        event_type = "pending_interaction_completed" if next_status == "completed" else "pending_interaction_expired"
        return [
            Event(
                tick=state.tick,
                event_type=event_type,
                actor_id=self.actor_id,
                payload={
                    "interaction_id": interaction.interaction_id,
                    "target_agent_id": interaction.target_agent_id,
                    "source_agent_id": interaction.source_agent_id,
                    "category": interaction.category,
                    "summary": interaction.summary,
                    "priority": interaction.priority,
                    "reason": interaction.resolution_reason,
                    "related_work_id": interaction.related_work_id,
                    "artifact_id": interaction.artifact_id,
                    "org_id": interaction.org_id,
                    "zone_id": interaction.zone_id,
                },
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
            return []
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
            "closes_tick": self.closes_tick,
        }
        if self.vote_type != "audit_review":
            payload["voters"] = list(self.voters)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        vote_opened_audience = [INTERNAL_AUDIENCE]
        if self.vote_type == "audit_review":
            vote_opened_audience = list(self.voters)
        events = [
            Event(
                tick=state.tick,
                event_type="vote_opened",
                actor_id=self.created_by,
                payload=payload,
                audience=vote_opened_audience,
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
