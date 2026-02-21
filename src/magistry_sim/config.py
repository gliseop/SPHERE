"""Модели конфигурации сценариев и профилей агентов."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import GovernanceMode, ScenarioId
from .personality import AgentPersonality


class Capability(BaseModel):
    """Полномочие агента: действие и типы дел, к которым оно применимо."""

    model_config = {"extra": "ignore"}

    action: str
    case_types: list[str] = Field(default_factory=list)


class Connection(BaseModel):
    """Связь агента с другим участником."""

    model_config = {"extra": "ignore"}

    target_id: str
    name: str
    relation: str
    strength: float = 1.0


class ResourcePool(BaseModel):
    """Начальные ресурсы агента."""

    model_config = {"extra": "ignore"}

    budget_limit: float = 0.0
    staffing_slots: int = 0
    contract_capacity: int = 0


class AgentProfile(BaseModel):
    """Профиль агента для конфигурации сценария."""

    model_config = {"extra": "ignore"}

    id: str
    name: str
    position: str
    capabilities: list[Capability] = Field(default_factory=list)
    greed: float = 0.5
    fear: float = 0.5
    honesty: float = 0.5
    competence: float | None = None
    personality: AgentPersonality | None = None
    immune: bool = False
    connections: list[Connection] = Field(default_factory=list)
    initial_resources: ResourcePool = Field(default_factory=ResourcePool)


class Need(BaseModel):
    """Потребность организации, возникающая в определённый раунд."""

    model_config = {"extra": "ignore"}

    case_type: str = ""
    description: str
    target_agent_id: str
    appear_round: int
    urgency: str = "средняя"


class GovernanceConfig(BaseModel):
    """Параметры режима управления."""

    model_config = {"extra": "ignore"}

    mode: GovernanceMode = GovernanceMode.G0
    jury_size: int = 3
    reputation_decay: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Коэффициент затухания репутации за раунд (1.0 = без затухания).",
    )


class ScenarioConfig(BaseModel):
    """Полная конфигурация сценария симуляции."""

    model_config = {"extra": "ignore"}

    id: ScenarioId
    title: str
    description: str
    max_rounds: int = 8
    seed: int = 42
    agents: list[AgentProfile] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    corruption_level: float = Field(default=0.0, ge=0.0, le=1.0)
    narrative_context: str = ""
