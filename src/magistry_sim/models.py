from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from magistry_sim.enums import AgentRole, GovernanceMode, ScenarioId


class GovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: GovernanceMode
    risk_threshold: float = 0.70
    critical_threshold: float = 0.85
    jury_size: int = 5


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ScenarioId
    title: str
    description: str

    ticks: int = 1
    seed: int = 0

    num_officials: int = 8
    num_contractors: int = 4

    tender_budget: float = 1_000_000.0

    relationship_strength: float = 0.0
    explicit_bribe: bool = False
    mask_language: bool = False
    enable_carousel: bool = False
    enable_timing_anomaly: bool = False
    noise_level: float = 0.0
    enable_adaptation: bool = False
    enable_bottom_up_signal: bool = False
    include_immune_influencer: bool = False


class SocialCapital(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work: float = 0.0
    research: float = 0.0
    social: float = 0.0

    @property
    def total(self) -> float:
        return self.work + self.research + self.social


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: AgentRole

    greed: float
    fear: float
    honesty: float
    competence: float

    immune: bool = False
    frozen: bool = False

    eligible_for_lpr: bool = True
    eligible_for_contracts: bool = True

    social_capital: SocialCapital = Field(default_factory=SocialCapital)


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")

    tick: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int
    lpr_id: str
    contractor_id: str
    risk_score: float
    reasons: list[str]
    critical: bool


class TickOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int
    tender_budget: float
    fair_winner_id: str
    actual_winner_id: str
    corruption: bool

    audit_risk: float | None = None
    audit_flagged: bool = False
    tribunal_triggered: bool = False
    tribunal_guilty: bool | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioId
    governance: GovernanceMode
    seed: int
    ticks: int

    outcomes: list[TickOutcome]
    final_agents: dict[str, Agent]
    events: list[Event]

