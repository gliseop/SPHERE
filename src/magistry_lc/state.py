"""WorldState для MAGISTRY-LC."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .entities import EntityRegistry
from .ids import EntityKind
from .memory import AgentMemory
from .persona import PersonaArtifact


@dataclass(slots=True)
class InstitutionRegimeState:
    """Операционный режим организации как части среды."""

    org_id: str
    operating_mode: str = "normal"
    transparency_mode: str = "routine"
    access_mode: str = "internal"
    security_mode: str = "routine"
    capture_risk: str = ""
    linked_zone_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ZoneState:
    """Состояние зоны/территории в среде."""

    zone_id: str
    title: str
    zone_type: str = "office"
    primary_org_id: str | None = None
    access_mode: str = "controlled"
    transparency_mode: str = "internal"
    security_level: str = "routine"


@dataclass(slots=True)
class ResourcePoolState:
    """Состояние ресурсного контура среды."""

    resource_id: str
    title: str
    owner_org_id: str | None = None
    quantity: float = 0.0
    unit: str = ""
    status: str = "stable"
    pressure: str = ""


@dataclass(slots=True)
class InformationClimateState:
    """Глобальный информационный фон мира."""

    public_mood: str = ""
    oversight_attention: str = ""
    media_pressure: str = ""
    narrative_temperature: str = ""
    active_signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EnvironmentState:
    """Самостоятельный слой состояния среды."""

    institutions: dict[str, InstitutionRegimeState] = field(default_factory=dict)
    zones: dict[str, ZoneState] = field(default_factory=dict)
    resource_pools: dict[str, ResourcePoolState] = field(default_factory=dict)
    information_climate: InformationClimateState = field(default_factory=InformationClimateState)

    def snapshot_dict(
        self,
        *,
        max_institutions: int = 12,
        max_zones: int = 12,
        max_resource_pools: int = 12,
    ) -> dict[str, Any]:
        """Вернуть компактный срез среды для журналов и worldgen."""
        return {
            "counts": {
                "institutions": len(self.institutions),
                "zones": len(self.zones),
                "resource_pools": len(self.resource_pools),
            },
            "institutions": [
                {
                    "org_id": item.org_id,
                    "operating_mode": item.operating_mode,
                    "transparency_mode": item.transparency_mode,
                    "access_mode": item.access_mode,
                    "security_mode": item.security_mode,
                    "capture_risk": item.capture_risk,
                    "linked_zone_ids": list(item.linked_zone_ids),
                }
                for _, item in sorted(self.institutions.items())[:max_institutions]
            ],
            "zones": [
                {
                    "zone_id": item.zone_id,
                    "title": item.title,
                    "zone_type": item.zone_type,
                    "primary_org_id": item.primary_org_id,
                    "access_mode": item.access_mode,
                    "transparency_mode": item.transparency_mode,
                    "security_level": item.security_level,
                }
                for _, item in sorted(self.zones.items())[:max_zones]
            ],
            "resource_pools": [
                {
                    "resource_id": item.resource_id,
                    "title": item.title,
                    "owner_org_id": item.owner_org_id,
                    "quantity": round(float(item.quantity), 3),
                    "unit": item.unit,
                    "status": item.status,
                    "pressure": item.pressure,
                }
                for _, item in sorted(self.resource_pools.items())[:max_resource_pools]
            ],
            "information_climate": {
                "public_mood": self.information_climate.public_mood,
                "oversight_attention": self.information_climate.oversight_attention,
                "media_pressure": self.information_climate.media_pressure,
                "narrative_temperature": self.information_climate.narrative_temperature,
                "active_signals": list(self.information_climate.active_signals),
            },
        }


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
    org_id: str | None = None
    zone_id: str | None = None

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
class ArtifactState:
    """Документ или артефакт как отдельная сущность мира."""

    artifact_id: str
    artifact_type: str
    title: str
    summary: str = ""
    owner_org_id: str | None = None
    zone_id: str | None = None
    related_work_id: str | None = None
    visibility: str = "internal"
    status: str = "active"
    tags: list[str] = field(default_factory=list)


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
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AuditCase:
    """Открытый или закрытый аудит-кейс runtime-аудита."""

    case_id: str
    finding_id: str
    created_tick: int
    subject_agent_id: str
    risk_family: str
    violation_type: str
    summary: str
    recommended_action: str
    confidence: float
    target_agent_id: str | None = None
    beneficiary: str | None = None
    related_agent_ids: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)
    episode_count: int = 1
    updated_tick: int | None = None
    last_finding_tick: int | None = None
    response_requested_tick: int | None = None
    response_due_tick: int | None = None

    status: str = "open"  # open|closed|review
    result: str | None = None
    result_reason: str = ""
    review_vote_id: str | None = None
    monitoring: bool = False


@dataclass(slots=True)
class WorldState:
    """Глобальное состояние мира."""

    tick: int = 0
    registry: EntityRegistry = field(default_factory=EntityRegistry)
    environment: EnvironmentState = field(default_factory=EnvironmentState)
    agents: dict[str, AgentState] = field(default_factory=dict)
    work_items: dict[str, WorkItem] = field(default_factory=dict)
    artifacts: dict[str, ArtifactState] = field(default_factory=dict)
    votes: dict[str, Vote] = field(default_factory=dict)
    audit_cases: dict[str, AuditCase] = field(default_factory=dict)

    def get_internal_agent_ids(self) -> list[str]:
        """Список внутренних агентов."""
        return sorted([aid for aid, a in self.agents.items() if a.internal])

    def clamp_reputation(self) -> None:
        """Применить инвариант репутации: reputation >= 0."""
        for a in self.agents.values():
            if a.reputation < 0:
                a.reputation = 0.0

    def journal_dict(
        self,
        *,
        max_work_items: int = 20,
        max_artifacts: int = 20,
        max_votes: int = 20,
    ) -> dict[str, Any]:
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
                    "metadata": dict(v.metadata),
                }
            )

        artifacts = []
        for art_id in sorted(self.artifacts.keys())[:max_artifacts]:
            artifact = self.artifacts[art_id]
            artifacts.append(
                {
                    "id": artifact.artifact_id,
                    "type": artifact.artifact_type,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "owner_org_id": artifact.owner_org_id,
                    "zone_id": artifact.zone_id,
                    "related_work_id": artifact.related_work_id,
                    "visibility": artifact.visibility,
                    "status": artifact.status,
                    "tags": list(artifact.tags),
                }
            )

        audit_cases = []
        for cid in sorted(self.audit_cases.keys())[:max_votes]:
            c = self.audit_cases[cid]
            audit_cases.append(
                {
                    "id": c.case_id,
                    "status": c.status,
                    "subject_agent_id": c.subject_agent_id,
                    "risk_family": c.risk_family,
                    "violation_type": c.violation_type,
                    "target_agent_id": c.target_agent_id,
                    "beneficiary": c.beneficiary,
                    "recommended_action": c.recommended_action,
                    "episode_count": c.episode_count,
                    "updated_tick": c.updated_tick,
                    "last_finding_tick": c.last_finding_tick,
                    "response_due_tick": c.response_due_tick,
                    "review_vote_id": c.review_vote_id,
                    "result": c.result,
                    "result_reason": c.result_reason,
                    "monitoring": c.monitoring,
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
                "zones": len(self.registry.list_ids(EntityKind.ZONE)),
                "resource_pools": len(self.registry.list_ids(EntityKind.RESOURCE)),
                "audit_cases": len(self.audit_cases),
            },
            "environment": self.environment.snapshot_dict(),
            "agents": agents,
            "work_items": work_items,
            "artifacts": artifacts,
            "votes": votes,
            "audit_cases": audit_cases,
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
